import asyncio
import json
import logging
import os
import re
import time
from datetime import datetime
from typing import Dict

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field, validator
from sqlalchemy.orm import Session

from .config import settings
from .context_optimizer import ContextOptimizer
from .database import get_db
from .deps import get_current_user
from .limiter import limiter
from .llm_provider import get_llm_provider
from .models import Segment, Token, User
from .services.segment_service import get_best_efforts_for_segment, save_segments_from_activity

router = APIRouter()
logger = logging.getLogger(__name__)

def format_seconds_to_str(seconds: int) -> str:
    """Format seconds into Mm Ss string."""
    if not seconds: return "0s"
    m = int(seconds // 60)
    s = int(seconds % 60)
    if m > 0: return f"{m}m {s:02d}s"
    return f"{s}s"

# LLM Configuration - defaults to OpenRouter
LLM_PROVIDER = settings.LLM_PROVIDER
LLM_MODEL = settings.LLM_MODEL

# --- GLOBAL STATE FOR THROTTLING ---
LAST_SEGMENT_SYNC = 0
SYNC_THRESHOLD = 7200 # 2 hours in seconds

MCP_SERVER_URL = os.getenv("MCP_SERVER_URL", "http://localhost:8001")

class QueryRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=1000)
    
    @validator('question')
    def validate_question(cls, v):
        if not v.strip():
            raise ValueError('Question cannot be empty')
        return v.strip()

class QueryResponse(BaseModel):
    answer: str
    data_used: dict

_token_refresh_locks: Dict[int, asyncio.Lock] = {}

async def get_valid_token(user: User, db: Session) -> str:
    """Get a valid access token, refreshing if necessary, with a lock to prevent race conditions."""
    lock = _token_refresh_locks.setdefault(user.id, asyncio.Lock())
    async with lock:
        # Re-fetch token from DB inside the lock to ensure we have the latest state
        token_entry = db.query(Token).filter(Token.user_id == user.id).first()
        if not token_entry:
            raise HTTPException(status_code=401, detail="No Strava tokens found for user")

        # Check expiration (with a 5-minute buffer)
        if datetime.utcnow().timestamp() > (token_entry.expires_at - 300):
            # Refresh token
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    "https://www.strava.com/oauth/token",
                    data={
                        "client_id": settings.STRAVA_CLIENT_ID,
                        "client_secret": settings.STRAVA_CLIENT_SECRET,
                        "refresh_token": token_entry.refresh_token,
                        "grant_type": "refresh_token",
                    },
                )
            
            if response.status_code != 200:
                # If refresh fails, the user must re-authenticate
                raise HTTPException(status_code=401, detail=f"Failed to refresh Strava token: {response.text}")

            data = response.json()
            token_entry.access_token = data["access_token"]
            token_entry.refresh_token = data["refresh_token"]
            token_entry.expires_at = data["expires_at"]
            db.add(token_entry)
            db.commit()
            db.refresh(token_entry)

        # Return the (potentially refreshed) access token
        return token_entry.access_token

def determine_query_type(question: str, optimized_context: dict) -> str:
    """Determine query type for smart model selection."""
    question_lower = question.lower()
    
    if any(word in question_lower for word in ['total', 'sum', 'average', 'how many', 'how much', 'count']):
        return "aggregate"
    elif any(word in question_lower for word in ['compare', 'vs', 'versus', 'difference', 'better', 'worse']):
        return "comparison"
    elif any(word in question_lower for word in ['analyze', 'trend', 'pattern', 'why', 'reason']):
        return "analysis"
    else:
        return "general"

@router.get("/status")
@limiter.limit("20/minute")
async def get_system_status(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get system status including hydration progress."""
    try:
        token = await get_valid_token(user, db)
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{MCP_SERVER_URL}/athlete/stats",
                headers={"X-Strava-Token": token}
            )
            if resp.status_code == 200:
                stats = resp.json()
                return {
                    "status": "online",
                    "sync": stats.get("app_status", {})
                }
            return {"status": "error", "message": "Failed to fetch stats from MCP"}
            
    except Exception as e:
        logger.error(f"Status check failed: {e}")
        return {"status": "error", "message": str(e)}

@router.post("/query", response_model=QueryResponse)
@limiter.limit("10/minute")
async def query_strava_data(
    request: Request,
    query: QueryRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):

    try:
        # 1. Get Valid Token
        access_token = await get_valid_token(user, db)

        # 1. Start background sync of starred segments to enable name matching
        try:
            from .services.segment_service import sync_starred_segments
            
            global LAST_SEGMENT_SYNC
            now = time.time()
            
            # If we have no segments, wait for the first sync to complete
            has_segments = db.query(Segment).first() is not None
            
            if not has_segments:
                logger.info("First run: Awaiting starred segment sync...")
                await sync_starred_segments(access_token, db)
                LAST_SEGMENT_SYNC = now
            elif (now - LAST_SEGMENT_SYNC) > SYNC_THRESHOLD:
                # Throttled background sync
                logger.info("Triggering throttled background segment sync...")
                asyncio.create_task(sync_starred_segments(access_token, db))
                LAST_SEGMENT_SYNC = now
            else:
                logger.debug("Skipping segment sync (throttled)")
        except Exception as e:
            logger.error(f"Failed to trigger starred segment sync: {e}")


        # 2. Fetch Context Data from MCP Server
        # For a generic query, we might fetch "recent activities" and "stats".
        # A more advanced implementation would let Gemini decide what to fetch via tool calls,
        # but per requirements, we'll fetch structured data and pass to Gemini.
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            headers = {"X-Strava-Token": access_token}
            
            # Parallel fetch for better performance
            try:
                stats_resp, activities_resp = await asyncio.gather(
                    client.get(f"{MCP_SERVER_URL}/athlete/stats", headers=headers, timeout=60.0),
                    client.get(f"{MCP_SERVER_URL}/activities/summary", headers=headers, timeout=180.0)
                )
                
                # Check directly for Rate Limits before processing
                if stats_resp.status_code == 429 or activities_resp.status_code == 429:
                    return JSONResponse(content={
                        "answer": "**Strava API Rate Limit Reached** 🚦\n\nStrava is currently limiting requests due to high traffic (likely during testing or full history sync). Please try again in approximately 15 minutes.\n\n*System Note: The backend is preventing further requests to avoid API bans.*",
                        "context": {},
                        "model": "system-alert"
                    })

            except httpx.RequestError as e:
                 raise HTTPException(status_code=500, detail=f"Failed to connect to MCP server: {str(e)}")

        stats_data = stats_resp.json() if stats_resp.status_code == 200 else {"error": "Failed to fetch stats"}
        activity_summary_data = activities_resp.json() if activities_resp.status_code == 200 else {"error": "Failed to fetch activities"}
        
        if "activities_by_date" in activity_summary_data:
             # print(f"DEBUG: Activity Days Count: {len(activity_summary_data['activities_by_date'])}", flush=True)
             pass

        context_data = {
            "stats": stats_data,
            "activity_summary": activity_summary_data
        }
        
        # 3. Optimize Context - Smart filtering to prevent context limits and minimize costs
        # 3. Optimize Context - Smart filtering to prevent context limits and minimize costs
        try:
            optimizer = ContextOptimizer(
                question=query.question,
                activity_summary=activity_summary_data,
                stats=stats_data
            )
            optimized_context = optimizer.optimize_context()
            
            # --- HYDRATION STRATEGY ---
            # We skip the "on-demand hydration" block here because we will perform
            # "detail enrichment" (which is more targeted and parallelized) later in this route.
            # This prevents duplicate MCP requests for the same activity IDs.

            
            # Log the strategy for debugging
            strategy = optimized_context.get('strategy')
            note = optimized_context.get('note')
            logger.info(f"Query: '{query.question}' | Strategy: {strategy} | Note: {note}")
            
            # SEGMENT CONTEXT INJECTION
            # Check if any persisted segments are mentioned in the query
            try:
                # 2. Check for explicit Segment ID or URL in query
                # Match https://www.strava.com/segments/12345 or just 12345 (if it looks like an ID context)
                id_match = re.search(r'segments/(\d+)', query.question)
                explicit_ids = [int(id_match.group(1))] if id_match else []

                # --- OPTIMIZED SEGMENT MATCHING ---
                # 1. Fuzzy match segment names from DB
                segment_trigger_words = ['segment', 'cr', 'kom', 'qom', 'leaderboard', 'rank', 'top', 'fastest', 'pr', 'personal record']
                has_segment_trigger = any(w in query.question.lower() for w in segment_trigger_words)
                has_quotes = '"' in query.question or "'" in query.question
                
                matched_segments = []
                if has_segment_trigger or has_quotes:
                    # 1. Look for quoted text (high confidence)
                    quoted_text = re.findall(r'["\'](.+?)["\']', query.question)
                    for term in quoted_text:
                        from .services.segment_service import search_segments
                        db_matches = search_segments(term, db, limit=3)
                        for seg in db_matches:
                            if (seg.id, seg.name) not in matched_segments:
                                matched_segments.append((seg.id, seg.name))
                    
                    # 2. Look for any known segment name that is in the question
                    # (This catches unquoted names like "...fastest time on Rose Bowl Loop...")
                    all_segments = db.query(Segment.id, Segment.name).all()
                    question_lower = query.question.lower()
                    for seg_id, seg_name in all_segments:
                        if not seg_name or len(seg_name) < 4: continue
                        
                        # Match if segment name is in question OR if significant part of name is in question
                        s_lower = seg_name.lower()
                        if s_lower in question_lower:
                            is_match = True
                        else:
                            # Split name into words and see if they appear together
                            words = [w for w in s_lower.split() if len(w) > 3]
                            is_match = len(words) >= 2 and all(w in question_lower for w in words)
                        
                        if is_match:
                            if not any(seg_id == m[0] for m in matched_segments):
                                matched_segments.append((seg_id, seg_name))
                                if len(matched_segments) >= 5: break
                # 2. Add explicit IDs from URL
                for eid in explicit_ids:
                    if not any(eid == m[0] for m in matched_segments):
                         matched_segments.append((eid, "Unknown Segment"))

                # CAP matched segments and fetch details/efforts
                matched_segments = matched_segments[:5]
                found_segments_data = []
                
                async with httpx.AsyncClient(timeout=15.0) as seg_client:
                    for seg_id, seg_name in matched_segments:
                        logger.info(f"Proactively fetching details for segment: {seg_name} ({seg_id})")
                        
                        # Fetch in parallel: Details and Leaderboard
                        detail_task = seg_client.get(f"{MCP_SERVER_URL}/segments/{seg_id}", headers=headers)
                        lb_task = seg_client.get(f"{MCP_SERVER_URL}/segments/{seg_id}/leaderboard", headers=headers)
                        
                        # Fetch ALL effort pages (Strava API paginates at 200 per page)
                        all_efforts = []
                        page = 1
                        while True:
                            efforts_resp = await seg_client.get(
                                f"{MCP_SERVER_URL}/segments/{seg_id}/efforts",
                                headers=headers,
                                params={"page": page, "per_page": 200}
                            )
                            if efforts_resp.status_code == 200:
                                page_efforts = efforts_resp.json()
                                if not page_efforts or not isinstance(page_efforts, list):
                                    break
                                all_efforts.extend(page_efforts)
                                logger.info(f"Segment {seg_name}: fetched page {page} with {len(page_efforts)} efforts (total: {len(all_efforts)})")
                                if len(page_efforts) < 200:  # Last page
                                    break
                                page += 1
                                await asyncio.sleep(0.5)  # Rate limit protection
                            else:
                                logger.warning(f"Failed to fetch efforts page {page} for segment {seg_id}: {efforts_resp.status_code}")
                                break
                        
                        resps = await asyncio.gather(detail_task, lb_task, return_exceptions=True)
                        
                        segment_details = resps[0].json() if isinstance(resps[0], httpx.Response) and resps[0].status_code == 200 else {}
                        leaderboard_data = resps[1].json() if isinstance(resps[1], httpx.Response) and resps[1].status_code == 200 else {}
                        effort_history = all_efforts

                        # Extract activity IDs from effort history and fetch activity summaries
                        # This allows AI to show which activities contain this segment
                        activity_ids = []
                        for e in effort_history:  # Show ALL efforts (no limit)
                            act_id = e.get("activity", {}).get("id") if isinstance(e.get("activity"), dict) else e.get("activity")
                            if act_id:
                                activity_ids.append(act_id)
                        
                        # Fetch activity summaries for these IDs from the activity summary data
                        segment_activities = []
                        if activity_ids:
                            # Look up activities from the already-fetched activity_summary data
                            for date_str, activities in activity_summary_data.get("activities_by_date", {}).items():
                                for act in activities:
                                    if act.get("id") in activity_ids:
                                        segment_activities.append({
                                            **act,
                                            "date": date_str
                                        })

                        found_segments_data.append({
                            "id": seg_id,
                            "name": segment_details.get("name", seg_name),
                            "details": {
                                "distance": segment_details.get("distance"),
                                "average_grade": segment_details.get("average_grade"),
                                "athlete_pr_effort": segment_details.get("athlete_pr_effort")
                            },
                            "leaderboard": {
                                "top_entries": leaderboard_data.get("entries", [])[:3],
                                "entry_count": leaderboard_data.get("entry_count")
                            },
                            "effort_history": [
                                {
                                    "activity_id": e.get("activity", {}).get("id") if isinstance(e.get("activity"), dict) else e.get("activity"),
                                    "date": e.get("start_date_local"),
                                    "time_str": format_seconds_to_str(e.get("elapsed_time")),
                                    "elapsed_time": e.get("elapsed_time"),
                                    "pr_rank": e.get("pr_rank")
                                } for e in effort_history  # Show ALL efforts (no limit)
                            ],
                            "activities_with_segment": segment_activities  # NEW: Full activity details
                        })
                if found_segments_data:
                    optimized_context["mentioned_segments"] = found_segments_data
            except Exception as e:
                logger.error(f"Segment logic failed: {e}")

            # --- DETAIL ENRICHMENT & ACTIVITY MATCHING ---
            # Match activities by name if the user mentions a specific run/route name like "Downskis"
            # Extract possible names from quotes or capitalized words
            potential_names = re.findall(r'["\'](.+?)["\']', query.question)
            if not potential_names:
                p_names = re.findall(r'\b[A-Z][A-Za-z0-9]+\b', query.question)
                if p_names: potential_names.append(" ".join(p_names))

            relevant_list = optimized_context.get("relevant_activities", [])
            named_matches = [act for act in relevant_list if any(name.lower() in act.get('name', '').lower() for name in potential_names)]
            needs_enrichment = any(w in query.question.lower() for w in ['note', 'desc', 'pain', 'detail', 'mention', 'say', 'with', 'segment', 'cr', 'kom', 'rank', 'what', 'yesterday', 'today', 'run', 'ride', 'exactly'])
            
            if relevant_list and (named_matches or needs_enrichment or len(relevant_list) <= 5):
                activities_to_enrich = named_matches[:3] if named_matches else relevant_list[:3]
                logger.info(f"Enriching {len(activities_to_enrich)} activities for query context...")
                # Prioritize activities that match query terms
                try:
                    query_lower = query.question.lower()
                    def relevance_score(act):
                        score = 0
                        full_text = f"{str(act.get('name', '')).lower()} {str(act.get('private_note', '')).lower()} {str(act.get('description', '')).lower()}"
                        stop_words = {'what', 'was', 'the', 'list', 'all', 'segments', 'from', 'at', 'in', 'on', 'my', 'run', 'ride', 'did', 'last'}
                        query_words = [w for w in re.findall(r'\w+', query.question.lower()) if w not in stop_words]
                        
                        # Content match
                        for w in query_words:
                            if w in full_text: score += 10
                            
                        # Distance match (very high priority for enrichment)
                        dist = act.get('distance_miles', 0)
                        for w in query_words:
                            if w.replace('.', '', 1).isdigit():
                                try:
                                    target_dist = float(w)
                                    if abs(dist - target_dist) < 0.1:
                                        score += 100 # Ensure distance matches are enriched
                                    elif abs(dist - target_dist) < 0.3:
                                        score += 50
                                except: pass
                        return (score, act.get('start_time', ''))

                    relevant_list.sort(key=relevance_score, reverse=True)
                except Exception as e:
                    logger.error(f"Relevance sorting failed: {e}")

                # CAP ENRICHMENT TO TOP 3 to prevent rate limits
                activities_to_enrich = relevant_list[:3]
                logger.info(f"Enriching top {len(activities_to_enrich)} activities (capped)...")
                
                try:
                    async with httpx.AsyncClient(timeout=30.0) as detail_client:
                        tasks = [detail_client.get(f"{MCP_SERVER_URL}/activities/{act['id']}", headers=headers) for act in activities_to_enrich]
                        responses = await asyncio.gather(*tasks, return_exceptions=True)
                        logger.info(f"Enrichment: Found {len(responses)} detail responses.")
                        for i, res in enumerate(responses):
                            if isinstance(res, httpx.Response) and res.status_code == 200:
                                detailed_data = res.json()
                                logger.info(f"Enriched activity {relevant_list[i].get('id')} with {len(detailed_data.get('segment_efforts', []))} segments.")
                                relevant_list[i].update({
                                    'private_note': detailed_data.get('private_note'),
                                    'description': detailed_data.get('description'),
                                    'name': detailed_data.get('name'),
                                    'segments': [
                                        {
                                            'name': s.get('name'), 
                                            'elapsed_time': f"{int(s.get('elapsed_time', 0)) // 60}:{int(s.get('elapsed_time', 0)) % 60:02d}",
                                            'id': s.get('segment', {}).get('id')
                                        } 
                                        for s in detailed_data.get('segment_efforts', [])[:15]
                                    ]
                                })
                                # Persist segments found here
                                try: save_segments_from_activity(detailed_data, db)
                                except Exception: pass
                            else:
                                logger.error(f"Failed to enrich activity {relevant_list[i].get('id')}: {res}")
                except Exception as e:
                    logger.error(f"Enrichment failed: {e}")

            # --- SUPPLEMENTAL SEGMENT MATCHING ---
            # If the user matched specific segments BY NAME but they weren't in enriched activities,
            # we fetch those individually (only if specifically requested).
            try:
                id_match = re.search(r'segments/(\d+)', query.question)
                explicit_ids = [int(id_match.group(1))] if id_match else []
                segment_trigger_words = ['cr', 'leaderboard', 'rank', 'top', 'fastest']
                needs_segment_api = any(w in query.question.lower() for w in segment_trigger_words)

                if (needs_segment_api or explicit_ids):
                    found_segments_data = optimized_context.get("mentioned_segments", [])
                    # (Rest of segment logic remains but only runs if explicit trigger found)
                    # For now, we rely on Activity Enrollment for standard "List all segments" queries.
                    pass
            except Exception: pass

            # GEAR & ZONES ENRICHMENT
            # If the user asks about heart rate zones, intensity, or gear (shoes/bikes).
            try:
                question_lower = query.question.lower()
                
                # ZONES
                if any(w in question_lower for w in ['zone', 'heart rate', 'power', 'intensity', 'distribution']):
                    # Fetch zones for the enriched/top activities (cap at 3 to be safe)
                    acts_to_zone = relevant_list[:3] if relevant_list else []
                    if acts_to_zone:
                        logger.info(f"Fetching zones for {len(acts_to_zone)} activities...")
                        async with httpx.AsyncClient(timeout=10.0) as zone_client:
                            tasks = [
                                zone_client.get(f"{MCP_SERVER_URL}/activities/{act['id']}/zones", headers=headers)
                                for act in acts_to_zone
                            ]
                            responses = await asyncio.gather(*tasks, return_exceptions=True)
                            
                            for i, res in enumerate(responses):
                                if isinstance(res, httpx.Response) and res.status_code == 200:
                                    # Inject zones into the activity object
                                    acts_to_zone[i]['zones'] = res.json()
                
                # GEAR
                if any(w in question_lower for w in ['shoe', 'bike', 'gear', 'equipment', 'mileage']):
                    # Check if activities have gear_id
                    # Also consider fetching the full gear list if needed, but usually stats has gear summaries.
                    # Let's check if we need specific gear details.
                    pass 
                    # Note: Athlete Stats (already fetched) contains `shoes` and `bikes` lists with names and total mileage.
                    # So we might not need to fetch individual gear unless we want specific details not in summary.
                    # We will ensure 'stats' is passed to context effectively.
                    
            except Exception as e:
                logger.error(f"Gear/Zone enrichment failed: {e}")
            
        except Exception as e:
            # import logging (removed to avoid shadowing)
            logging.getLogger(__name__).error(f"Context optimization failed: {str(e)}", exc_info=True)
            # Fallback to simple context
            optimized_context = {
                "stats": stats_data,
                "summary": "Context optimization failed, returning limited data.",
                "error": str(e)
            }
        
        # System instructions (reduces token cost, can be cached)
        system_instruction = """You are a helpful assistant analyzing Strava fitness data. You MUST strictly follow the MANDATORY OUTPUT RULES provided in the user prompt.

IMPORTANT INSTRUCTIONS:
- **DATA FIELDS**: The activity data provided uses specific field names:
  - `id`: Unique Activity ID.
  - `distance_miles`: Distance of the activity in miles.
  - `elevation_feet`: Elevation gain in feet.
  - `moving_time_seconds`: Moving time in seconds (convert to hours/minutes for display, e.g. "4h 30m").
  - `elapsed_time_str`: Pre-formatted elapsed time string (e.g., "26h 8m"). **ALWAYS USE THIS FIELD** for elapsed time. Do not calculate from seconds.
  - `elapsed_time_seconds`: Total elapsed time in seconds. Ignored in favor of `elapsed_time_str`.
  - `type`: Activity type (e.g., Run, Ride, TrailRun).
  - For "list/show activities" queries: Use the `activities_with_segment` array which contains full activity details.
  - `athlete_count`: Number of athletes in the group. Use `athlete_count > 1` to identify runs with others.
  - `route_match_count`: Total number of times this specific route has been run. To find "other" runs on this route, subtract 1.
  - `name`: Name of the activity.
  - `date`: Date of the activity (YYYY-MM-DD).
  - `segments`: List of segments. Format times as Minutes:Seconds (e.g., "12:30").

- **LINKING & FORMATTING**:
  - **ACTIVITY STRUCTURE**: 
    1. Start with the Activity Name as a Heading 3 link: `### [Activity Name](https://www.strava.com/activities/{id})`
    2. Follow with the Date: `**Date**: {date}` (MANDATORY)
    3. Follow with stats: `- **Distance**: 5.2 miles`, etc.
    4. **MAP LINK**: DO NOT INCLUDE ANY MAP LINKS.
    5. **SEGMENTS**: If segments are in the data, list them under `#### Top Segments` as bullet points with links:
       - Format: `- [Segment Name](https://www.strava.com/segments/{segment_id}) - {time}`
       - Example: `- [Big Hill Climb](https://www.strava.com/segments/12345) - 12:30`
- **SEGMENT EFFORT HISTORY**:
  - If the data includes `mentioned_segments` with `effort_history`, USE THIS DATA to answer "first time", "last time", or "how many times" questions.
  - The `effort_history` array contains ALL your attempts on that segment, sorted chronologically.
  - For "first time" queries: Use the FIRST entry in effort_history (oldest date).
  - For "last time" queries: Use the LAST entry in effort_history (most recent date).
  - Each effort includes `activity_id` - use this to create activity links: `https://www.strava.com/activities/{activity_id}`
  - For "how many times" queries: Count the entries in effort_history.
  - **CRITICAL**: Do NOT show a random activity - use the specific date from effort_history.
- **DATA ANALYSIS**: 
  - **DISTANCES**: If you are searching for an "exactly X miles" run, and the data shows X.008 or X.992, you MUST report it as exactly "X.0 miles". Strava UI rounds to 1 decimal place, so match that look.
  - **DATES**: Every activity summary MUST start with the full date (e.g. August 2, 2025).
  - **NO HOSTNAMES**: NEVER use `localhost`, `127.0.0.1`, or `8001`. **ABSOLUTELY NO PROTOCOLS OR HOSTS**.
- **TONE**: Provide concise and encouraging responses. """

        # User prompt (minimal, dynamic content)
        # User prompt (minimal, dynamic content)
        # Ensure context is valid JSON for the LLM
        # 5. Sanitize context for security and robust linking
        # Remove any internal URL patterns that confuse the LLM into generating bad links
        context_json = json.dumps(optimized_context, indent=2, default=str)
        # Restore and harden sanitation - remove any URL that looks like a map or localhost
        context_json = re.sub(r'https?://(?:localhost|127\.0\.0\.1|8001|\[INTERNAL_RESOURCE\]).*?(?=\s|$|\"|\))', '[REMOVED]', context_json)
        context_json = re.sub(r'View Interactive Map', '[REMOVED]', context_json)
        
        user_prompt = f"""### MANDATORY OUTPUT RULES:
1. **DATES**: Every activity summary MUST start with the human-readable date (e.g., "August 2, 2025").
2. **ACTIVITY TITLES**: Always link activity names as Heading 3: `### [Activity Name](https://www.strava.com/activities/{id})`
3. **SEGMENTS**: List segments under `#### Top Segments`. If no segments are in the data, state "No segments found".
4. **DISTANCE DISPLAY**: For "exactly" queries, round to 1 decimal place (e.g. "5.0 miles") if the data is within 0.05 miles of the target.


=== USER QUESTION ===
{query.question}
=== END USER QUESTION ===

=== DATA ===
{context_json}
=== END DATA ===

Answer the user's question following the MANDATORY RULES above.
"""
        
        # 4. Generate Answer using LLM provider (OpenRouter, DeepSeek, or Gemini)
        
        # Check Cache
        import hashlib

        from .models import LLMCache
        
        # Hash both prompt AND instructions to avoid stale cached formatting
        combined_prompt = f"{system_instruction}\n\n{user_prompt}"
        prompt_hash = hashlib.sha256(combined_prompt.encode()).hexdigest()
        cached_entry = db.query(LLMCache).filter(LLMCache.prompt_hash == prompt_hash).first()
        
        if cached_entry:
            logger.info("Returning cached LLM response")
            return QueryResponse(answer=cached_entry.response, data_used=context_data)

        try:
            llm = get_llm_provider()
            
            # Determine query type for smart model selection (OpenRouter only)
            query_type = determine_query_type(query.question, optimized_context)
            
            answer_text = await llm.generate(
                prompt=user_prompt,
                system_instruction=system_instruction,
                temperature=0.3,
                max_tokens=2000,
                query_type=query_type  # For smart model selection with OpenRouter
            )
            
            # Save to Cache
            new_cache = LLMCache(prompt_hash=prompt_hash, response=answer_text)
            db.add(new_cache)
            db.commit()
            
        except ValueError as e:
            # Configuration error
            raise HTTPException(
                status_code=500, 
                detail=f"LLM configuration error: {str(e)}. Please check your API keys in .env"
            )
        except Exception as e:
            # Handle context limit errors gracefully
            error_msg = str(e).lower()
            error_str = str(e)
            
            # Log the full error for debugging
            # import logging (removed to avoid shadowing)
            logger.error(f"LLM generation error: {error_str}")
            
            if "context" in error_msg or "token" in error_msg or "length" in error_msg:
                answer_text = "I apologize, but the query requires too much data to process at once. Please try a more specific question or a shorter time range."
            elif "404" in error_str or "not found" in error_msg:
                # Check if it's an OpenRouter model availability issue
                raise HTTPException(
                    status_code=500,
                    detail=f"Model not available: {error_str}. The model '{LLM_MODEL}' may not be accessible with your API key. Try a different model in .env (e.g., google/gemini-3-flash-preview)."
                )
            elif "api" in error_msg or "key" in error_msg or "auth" in error_msg:
                raise HTTPException(
                    status_code=500,
                    detail=f"LLM API error: {error_str}. Please check your API key configuration."
                )
            else:
                answer_text = f"Error generating answer: {error_str}"
        
        return QueryResponse(answer=answer_text, data_used=context_data)
        
    except HTTPException as he:
        raise he
    except Exception as e:
        import traceback
        print(f"CRITICAL ROUTE ERROR: {str(e)}\n{traceback.format_exc()}", flush=True)
        raise HTTPException(status_code=500, detail="Internal Server Error detected in route handler.")

@router.get("/activities/{activity_id}/map")
async def get_activity_map(
    activity_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Proxy map request to MCP server."""
    logger.info(f"Map request received for {activity_id} from user {user.id}")
    token = await get_valid_token(user, db)
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            logger.info(f"Fetching map from MCP: {MCP_SERVER_URL}/activities/{activity_id}/map")
            response = await client.get(
                f"{MCP_SERVER_URL}/activities/{activity_id}/map",
                headers={"X-Strava-Token": token}
            )
            if response.status_code != 200:
                logger.error(f"MCP Map error: {response.status_code} - {response.text[:100]}")
                raise HTTPException(status_code=response.status_code, detail="Failed to fetch map from MCP")
            
            logger.info(f"Map delivered for {activity_id}")
            return HTMLResponse(content=response.text)
        except Exception as e:
            logger.error(f"Map proxy failed: {e}")
            raise HTTPException(status_code=500, detail=str(e))

@router.get("/test-data")
async def get_test_data(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Utility to see what data the backend fetches for debugging."""
    access_token = await get_valid_token(user, db)
    async with httpx.AsyncClient() as client:
        headers = {"X-Strava-Token": access_token}
        stats_resp = await client.get(f"{MCP_SERVER_URL}/athlete/stats", headers=headers)
        activities_resp = await client.get(f"{MCP_SERVER_URL}/activities/recent?limit=10", headers=headers)
        
    return {
        "stats": stats_resp.json(),
        "recent_activities": activities_resp.json()
    }
