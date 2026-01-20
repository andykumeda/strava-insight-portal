import os
import re
import asyncio
import httpx
import logging
import json
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, validator
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
from typing import Dict

from .database import get_db
from .models import User, Token, Segment
from .deps import get_current_user
from .context_optimizer import ContextOptimizer
from .services.segment_service import save_segments_from_activity, get_best_efforts_for_segment
from .llm_provider import get_llm_provider
from .config import settings
from .limiter import limiter
router = APIRouter()

# LLM Configuration - defaults to OpenRouter
LLM_PROVIDER = settings.LLM_PROVIDER
LLM_MODEL = settings.LLM_MODEL

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

        # 2. Fetch Context Data from MCP Server
        # For a generic query, we might fetch "recent activities" and "stats".
        # A more advanced implementation would let Gemini decide what to fetch via tool calls,
        # but per requirements, we'll fetch structured data and pass to Gemini.
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            headers = {"X-Strava-Token": access_token}
            
            # Parallel fetch for better performance
            try:
                stats_resp, activities_resp = await asyncio.gather(
                    client.get(f"{MCP_SERVER_URL}/athlete/stats", headers=headers),
                    client.get(f"{MCP_SERVER_URL}/activities/summary", headers=headers, timeout=180.0)
                )
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
            
            # Log the strategy for debugging
            logger = logging.getLogger(__name__)
            strategy = optimized_context.get('strategy')
            note = optimized_context.get('note')
            logger.info(f"Query: '{query.question}' | Strategy: {strategy} | Note: {note}")
            
            # SEGMENT CONTEXT INJECTION
            # Check if any persisted segments are mentioned in the query
            try:
                # 1. Check for basic fuzzy match names
                all_segments = db.query(Segment).with_entities(Segment.id, Segment.name).all()
                
                # 2. Check for explicit Segment ID or URL in query
                # Match https://www.strava.com/segments/12345 or just 12345 (if it looks like an ID context)
                id_match = re.search(r'segments/(\d+)', query.question)
                explicit_ids = [int(id_match.group(1))] if id_match else []

                # Combine explicit IDs with text-matched IDs
                matched_segments = []
                for seg_id, seg_name in all_segments:
                    if seg_name and seg_name.lower() in query.question.lower():
                        matched_segments.append((seg_id, seg_name))
                
                # Add explicit IDs if not already found (fetches name dynamically if needed)
                for eid in explicit_ids:
                    if not any(eid == m[0] for m in matched_segments):
                         matched_segments.append((eid, "Unknown Segment"))

                found_segments_data = [] # Initialize here!
                for seg_id, seg_name in matched_segments:
                    logger.info(f"Processing segment: {seg_name} ({seg_id})")
                        
                    # Fetch authoritative segment details (including PR)
                    segment_details = {}
                    leaderboard_data = {}
                    
                    try:
                        async with httpx.AsyncClient(timeout=10.0) as seg_client:
                            # A. Details
                            seg_resp = await seg_client.get(
                                f"{MCP_SERVER_URL}/segments/{seg_id}",
                                headers=headers
                            )
                            if seg_resp.status_code == 200:
                                segment_details = seg_resp.json()
                                # Update name if it was unknown
                                if seg_name == "Unknown Segment":
                                    seg_name = segment_details.get("name", "Unknown Segment")
                                logger.info(f"Fetched details for {seg_id}.")
                            
                            # B. Leaderboard (CR)
                            if any(w in query.question.lower() for w in ['cr', 'kom', 'qom', 'leader', 'fastest', 'rank', 'who']):
                                lb_resp = await seg_client.get(
                                    f"{MCP_SERVER_URL}/segments/{seg_id}/leaderboard",
                                    headers=headers
                                )
                                print(f"DEBUG: Leaderboard Status: {lb_resp.status_code}")
                                if lb_resp.status_code == 200:
                                    leaderboard_data = lb_resp.json()
                                    print(f"DEBUG: Leaderboard Data: {leaderboard_data}")
                                else:
                                    print(f"DEBUG: Failed to fetch leaderboard: {lb_resp.text}")

                    except Exception as e:
                        print(f"DEBUG: Exception in segment fetch: {e}")

                    # Check for history request
                    segment_history = []
                    if any(w in query.question.lower() for w in ['list', 'history', 'all times', 'previous times', 'efforts', 'past']):
                             try:
                                logger.info(f"Fetching full history for segment {seg_id}...")
                                async with httpx.AsyncClient(timeout=10.0) as hist_client:
                                    hist_resp = await hist_client.get(
                                        f"{MCP_SERVER_URL}/segments/{seg_id}/efforts",
                                        params={"per_page": 30},
                                        headers=headers
                                    )
                                    if hist_resp.status_code == 200:
                                        raw_history = hist_resp.json()
                                        segment_history = [
                                            {
                                                "date": h.get("start_date_local", "Unknown")[:10],
                                                "elapsed_time_seconds": h.get("elapsed_time"),
                                                "moving_time_seconds": h.get("moving_time"),
                                                "rank": h.get("kom_rank") or h.get("pr_rank")
                                            } for h in raw_history
                                        ]
                                        logger.info(f"Fetched {len(segment_history)} historical efforts.")
                             except Exception as e:
                                logger.error(f"Failed to fetch history for segment {seg_id}: {e}")

                    efforts = get_best_efforts_for_segment(seg_id, db)
                    
                    found_segments_data.append({
                        "name": seg_name,
                        "id": seg_id,
                        "details": {
                            "distance": segment_details.get("distance"),
                            "average_grade": segment_details.get("average_grade"),
                            "athlete_pr_effort": segment_details.get("athlete_pr_effort")
                        },
                        "leaderboard": {
                            "top_entries": leaderboard_data.get("entries", [])[:3], # Top 3 leaders
                            "entry_count": leaderboard_data.get("entry_count")
                        },
                        "history": segment_history, 
                        "recent_db_efforts": [
                            {
                                "date": e.start_date.strftime("%Y-%m-%d") if e.start_date else "Unknown",
                                "elapsed_time_seconds": e.elapsed_time,
                                "pr_rank": e.pr_rank
                            } for e in efforts
                        ]
                    })
                
                if found_segments_data:
                    optimized_context["mentioned_segments"] = found_segments_data
            except Exception as e:
                logger.error(f"Segment lookup failed: {e}")

            # DETAIL ENRICHMENT:
            # If the user asks about notes/descriptions OR we have a small number of activities (<= 5),
            # fetch the full details (which contain private_note and segments) to enrich the context.
            # This ensures segments are synced more often and short queries get high fidelity.
            relevant = optimized_context.get("relevant_activities", [])
            needs_enrichment = any(w in query.question.lower() for w in ['note', 'desc', 'pain', 'detail', 'mention', 'say', 'with', 'segment'])
            is_small_set = len(relevant) <= 5
            
            if relevant and (needs_enrichment or is_small_set) and len(relevant) < 50:
                logger.info(f"Enriching {len(relevant)} activities with full details (for notes/desc/segments)...")
                try:
                    async with httpx.AsyncClient(timeout=30.0) as detail_client:
                        # Fetch details in parallel
                        tasks = [
                            detail_client.get(f"{MCP_SERVER_URL}/activities/{act['id']}", headers=headers)
                            for act in relevant
                        ]
                        responses = await asyncio.gather(*tasks, return_exceptions=True)
                        
                        # Merge details back
                        for i, res in enumerate(responses):
                            if isinstance(res, httpx.Response) and res.status_code == 200:
                                detailed_data = res.json()
                                # Update the relevant activity with detailed fields
                                relevant[i]['private_note'] = detailed_data.get('private_note')
                                relevant[i]['description'] = detailed_data.get('description')
                                # Also update name/type just in case
                                relevant[i]['name'] = detailed_data.get('name')
                                
                                # Inject top segments for context
                                segs = detailed_data.get('segment_efforts', [])
                                relevant[i]['segments'] = [
                                    {
                                        'name': s.get('name'), 
                                        'elapsed_time': s.get('elapsed_time'),
                                        'id': s.get('segment', {}).get('id')
                                    } 
                                    for s in segs[:15]
                                ]
                                
                                # Persist segments found in this detailed activity
                                try:
                                    save_segments_from_activity(detailed_data, db)
                                except Exception as e:
                                    logger.error(f"Failed to save segments for activity {detailed_data.get('id')}: {e}")
                except Exception as e:
                    logger.error(f"Enrichment failed: {e}")
            
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
        system_instruction = """You are a helpful assistant analyzing Strava fitness data.

IMPORTANT INSTRUCTIONS:
- **DATA FIELDS**: The activity data provided uses specific field names:
  - `distance_miles`: Distance of the activity in miles.
  - `elevation_feet`: Elevation gain in feet.
  - `moving_time_seconds`: Moving time in seconds (convert to hours/minutes for display).
  - `type`: Activity type (e.g., Run, Ride, TrailRun).
  - `name`: Name of the activity.
  - `date`: Date of the activity (YYYY-MM-DD).
  - `segments`: List of segments (name, elapsed_time, id) for detailed activities. Format `elapsed_time` (seconds) as minutes:seconds (e.g., "5:30").

- **LINKING & FORMATTING**:
  - **ACTIVITY STRUCTURE**: 
    1. Start with the Activity Name as a Heading 3 link: `### [Activity Name](https://www.strava.com/activities/{id})`
    2. Follow with a bulleted list for stats (Distance, Elevation, Moving Time).
    3. If listing segments, use Heading 4: `#### Top Segments`
    4. List segments as bullet points with links: `- [Segment Name](https://www.strava.com/segments/{id}) - {elapsed_time}`
  - **EXAMPLE**:
    ### [Morning Run](https://www.strava.com/activities/12345)
    - **Distance**: 5.2 miles
    - **Elevation**: 400 ft
    - **Time**: 45:30
    #### Top Segments
    - [Big Hill](https://www.strava.com/segments/987) - 12:30
    - [Sprint Finish](https://www.strava.com/segments/654) - 0:45

- **COMPARISONS**: When comparing years, only compare matching time periods.
- **CALCULATIONS**: You are a data analyst. If the user asks for aggregates (e.g., "weekly mileage") and you have a list of activities, YOU MUST CALCULATE the aggregates yourself by summing the relevant fields (e.g., `distance_miles`) for the requested time periods. Do not say data is missing if you have the list of activities.
- **SUMMARIES**: Use summary_by_year for aggregate queries when detailed activities aren't provided.
- **TONE**: Provide concise and encouraging responses."""

        # User prompt (minimal, dynamic content)
        # User prompt (minimal, dynamic content)
        # Ensure context is valid JSON for the LLM
        context_json = json.dumps(optimized_context, indent=2, default=str)
        
        user_prompt = f"""=== USER QUESTION ===
{query.question}
=== END USER QUESTION ===

=== DATA ===
{context_json}
=== END DATA ===

Answer the user's question based on this data. If the answer cannot be determined from the data, say so."""
        
        # 4. Generate Answer using LLM provider (OpenRouter, DeepSeek, or Gemini)
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
            logger = logging.getLogger(__name__)
            logger.error(f"LLM generation error: {error_str}")
            
            if "context" in error_msg or "token" in error_msg or "length" in error_msg:
                answer_text = f"I apologize, but the query requires too much data to process at once. Please try a more specific question or a shorter time range."
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
