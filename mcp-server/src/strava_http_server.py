#!/usr/bin/env python3
"""
HTTP server for Strava API integration.
This server exposes HTTP endpoints to query the Strava API for activities, athletes, and other data.
"""

import os
import sys
import time
import asyncio
from typing import Any, Dict, List, Optional
from collections import defaultdict
from datetime import datetime
import json
import logging
from fastapi import FastAPI, HTTPException, Response, Header, BackgroundTasks
from pydantic import BaseModel
from fastapi.responses import HTMLResponse
import uvicorn
import httpx
from map_utils import format_activity_with_map
from rate_limiter import rate_limiter

# Configure logging
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    stream=sys.stderr
)
logger = logging.getLogger(__name__)

# Strava API configuration
STRAVA_API_BASE_URL = "https://www.strava.com/api/v3"

# In-memory cache for activities
# Cache structure: {athlete_id: {"activities": [...], "fetched_at": timestamp}}
ACTIVITY_CACHE: Dict[str, Dict[str, Any]] = {}

# Cache structure: {token: athlete_id}
TOKEN_TO_ID_CACHE: Dict[str, str] = {}
LAST_HYDRATION_TRIGGER = 0  # Timestamp of last background hydration start
ATHLETE_LOCKS = defaultdict(asyncio.Lock)

# --- SEGMENT CACHE ---
# Cache for segment details, efforts, and leaderboards
# {segment_id: {"details": {...}, "leaderboard": {...}, "efforts": [...], "fetched_at": timestamp}}
SEGMENT_CACHE: Dict[int, Dict[str, Any]] = {}
SEGMENT_TTL = 3600 * 24 # 24 hours for segment details (they don't change often)
SEGMENT_EFFORTS_TTL = 3600 * 1 # 1 hour for efforts/leaderboard


# Cache configuration
CACHE_FILE = "strava_cache.json"

class HydrationRequest(BaseModel):
    ids: List[int]
CACHE_TTL_SECONDS = 3600  # 1 hour
STARRED_SEGMENTS_TTL = 3600 * 24 # 24 hours for starred segments list

def format_seconds_to_str(seconds: int) -> str:
    """Format seconds into Xh Ym string."""
    if not seconds:
        return "0s"
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    return f"{hours}h {minutes}m"

def load_cache_from_disk():
    """Load activity cache from disk."""
    global ACTIVITY_CACHE
    try:
        if os.path.exists(CACHE_FILE):
            with open(CACHE_FILE, 'r') as f:
                ACTIVITY_CACHE = json.load(f)
            logger.info(f"Loaded {len(ACTIVITY_CACHE)} athletes from disk cache.")
    except Exception as e:
        logger.error(f"Failed to load disk cache: {e}")

def save_cache_to_disk():
    """Save activity cache to disk."""
    try:
        with open(CACHE_FILE, 'w') as f:
            json.dump(ACTIVITY_CACHE, f)
        logger.info("Saved cache to disk.")
    except Exception as e:
        logger.error(f"Failed to save disk cache: {e}")

# Load cache on startup
load_cache_from_disk()

# Create FastAPI app
app = FastAPI(
    title="Strava API Server",
    description="HTTP server for Strava API integration",
)

async def make_strava_request(url: str, method: str = "GET", params: Dict[str, Any] = None, access_token: str = None, response_type: str = "json") -> Any:
    """
    Make a request to the Strava API with STRICT Rate Limiting.
    response_type: "json" (default), "text", or "content" (binary)
    """
    if not access_token:
        raise HTTPException(status_code=401, detail="Missing X-Strava-Token header")
    
    # Retry logic for 429s (Rate Limit Exceeded)
    max_retries = 3
    retry_count = 0
    
    headers = {"Authorization": f"Bearer {access_token}"}
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        while True:
            # 1. CHECK RATE LIMITS BEFORE EVERY ATTEMPT
            if not rate_limiter.can_request():
                stats = rate_limiter.get_stats()
                msg = f"Rate Limit Reached (Internal Safety). Used: 15m={stats['15m_used']}, Daily={stats['daily_used']}"
                logger.error(msg)
                raise HTTPException(status_code=429, detail=msg)

            # 2. RECORD THE ATTEMPT IMMEDIATELY
            rate_limiter.record_attempt()

            try:
                response = await client.request(
                    method=method,
                    url=url,
                    headers=headers,
                    params=params
                )
                
                # Check for 429 Rate Limit from Strava
                if response.status_code == 429:
                    # Strava is telling us we overshot. 
                    # We need to aggressively stop everything.
                    logger.error("!!! STRAVA 429 RECEIVED. Aggressively halting all further requests. !!!")
                    
                    # Force the rate limiter to reflect the overload so can_request() fails for everyone
                    for _ in range(rate_limiter.LIMIT_15_MIN):
                        rate_limiter.record_attempt()

                    raise HTTPException(status_code=429, detail="Strava API Rate Limit Exceeded (Global Lockout)")

                if response.status_code == 401:
                     raise HTTPException(status_code=401, detail="Invalid or expired Strava token")
                
                response.raise_for_status()
                
                if response_type == "text":
                    return response.text
                elif response_type == "content":
                    return response.content
                return response.json()
                
            except httpx.HTTPStatusError as e:
                logger.error(f"Strava API request failed: {str(e)}")
                raise HTTPException(status_code=e.response.status_code, detail=str(e))
            except httpx.RequestError as e:
                logger.error(f"Strava API connection error: {str(e)}")
                raise HTTPException(status_code=500, detail=f"Connection error: {str(e)}")
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Unexpected error during Strava request: {str(e)}")
                raise HTTPException(status_code=500, detail=str(e))

@app.get("/auth/status")
async def check_auth_status(x_strava_token: Optional[str] = Header(None, alias="X-Strava-Token")) -> Dict[str, Any]:
    """Check if we're authenticated with Strava."""
    if not x_strava_token:
        return {
            "authenticated": False,
            "message": "Not authenticated with Strava"
        }
    
    try:
        profile = await make_strava_request(f"{STRAVA_API_BASE_URL}/athlete", access_token=x_strava_token)
        return {
            "authenticated": True,
            "message": "Successfully authenticated with Strava",
            "profile": profile
        }
    except Exception as e:
        return {
            "authenticated": False,
            "message": f"Authentication error: {str(e)}"
        }

@app.get("/activities/recent")
async def get_recent_activities(limit: int = 200, page: int = 1, x_strava_token: str = Header(..., alias="X-Strava-Token")) -> List[Dict[str, Any]]:
    """Get recent activities from Strava. Max 200 per page."""
    # Strava API max is 200 per page
    per_page = min(limit, 200)
    return await make_strava_request(
        f"{STRAVA_API_BASE_URL}/athlete/activities",
        params={"per_page": per_page, "page": page},
        access_token=x_strava_token
    )

@app.get("/activities/search")
async def search_activities_optimized(
    x_strava_token: str = Header(..., alias="X-Strava-Token"),
    oldest_first: bool = False,
    max_pages: int = 25,
    search_name: Optional[str] = None,
    min_distance_meters: Optional[float] = None,
    max_distance_meters: Optional[float] = None,
    activity_type: Optional[str] = None,
    after_date: Optional[str] = None,
    before_date: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Search activities with optimized fetching strategy.
    
    - oldest_first: If True, fetches oldest activities first using 'after' param.
                    Use this for 'first time I did X' queries to enable early stopping.
    - max_pages: Maximum pages to fetch (default 25 = ~5000 activities)
    - search_name: Filter by activity name (case-insensitive substring match)
    - min_distance_meters/max_distance_meters: Filter by distance range
    - activity_type: Filter by type (Run, Ride, Swim, etc.)
    - after_date/before_date: Date range filter (YYYY-MM-DD format)
    
    Returns: {activities: [...], pages_fetched: int, early_stopped: bool, total_found: int}
    """
    import datetime
    
    all_matches = []
    page = 1
    early_stopped = False
    
    # For oldest-first, use 'after' param with timestamp from year 2000
    # This reverses the sort order to chronological (oldest first)
    base_params = {"per_page": 200}
    
    if oldest_first:
        # Unix timestamp for Jan 1, 2000 = 946684800
        base_params["after"] = 946684800
    
    # Add date filters if provided
    if after_date:
        try:
            dt = datetime.datetime.strptime(after_date, "%Y-%m-%d")
            base_params["after"] = int(dt.timestamp())
        except ValueError:
            pass
    
    if before_date:
        try:
            dt = datetime.datetime.strptime(before_date, "%Y-%m-%d")
            base_params["before"] = int(dt.timestamp())
        except ValueError:
            pass
    
    while page <= max_pages:
        params = {**base_params, "page": page}
        logger.info(f"Search: Fetching page {page}, oldest_first={oldest_first}")
        
        try:
            activities = await make_strava_request(
                f"{STRAVA_API_BASE_URL}/athlete/activities",
                params=params,
                access_token=x_strava_token
            )
        except Exception as e:
            logger.warning(f"Search stopped at page {page}: {e}")
            break
        
        if not activities:
            break
        
        # Filter activities
        for act in activities:
            match = True
            
            if search_name and search_name.lower() not in act.get("name", "").lower():
                match = False
            
            if activity_type and act.get("type", "").lower() != activity_type.lower():
                match = False
            
            dist = act.get("distance", 0)
            if min_distance_meters and dist < min_distance_meters:
                match = False
            if max_distance_meters and dist > max_distance_meters:
                match = False
            
            if match:
                all_matches.append(act)
                
                # For oldest-first "first occurrence" queries, we can stop early
                # after finding the first match (user can specify via max results)
                if oldest_first and len(all_matches) >= 1:
                    early_stopped = True
                    break
        
        if early_stopped:
            break
            
        if len(activities) < 200:
            # Last page reached
            break
            
        page += 1
    
    return {
        "activities": all_matches,
        "pages_fetched": page,
        "early_stopped": early_stopped,
        "total_found": len(all_matches),
        "strategy": "oldest_first" if oldest_first else "newest_first"
    }

async def _fetch_all_activities_logic(x_strava_token: str, refresh: bool) -> List[Dict[str, Any]]:
    """Core logic to fetch all activities, separated for background reuse."""
    global ACTIVITY_CACHE, TOKEN_TO_ID_CACHE
    
    # Get athlete ID (check token cache first)
    athlete_id = TOKEN_TO_ID_CACHE.get(x_strava_token)
    
    if not athlete_id:
        try:
            athlete = await make_strava_request(f"{STRAVA_API_BASE_URL}/athlete", access_token=x_strava_token)
            athlete_id = str(athlete["id"])
            TOKEN_TO_ID_CACHE[x_strava_token] = athlete_id
        except HTTPException as e:
            if e.status_code == 429:
                logger.error("Rate limited getting athlete ID. Cannot check cache.")
            raise e
    
    # Check cache
    if athlete_id in ACTIVITY_CACHE:
        cache_entry = ACTIVITY_CACHE[athlete_id]
        age = time.time() - cache_entry["fetched_at"]
        
        # If cache is valid, return immediately
        if age < CACHE_TTL_SECONDS and not refresh:
            logger.info(f"Returning {len(cache_entry['activities'])} cached activities for athlete {athlete_id}")
            return cache_entry["activities"]
            
        # If cache is stale but exists, and we are NOT explicitly refreshing (just reading),
        # return stale data but trigger background refresh if needed? 
        # Actually, for async, we can just return stale data here if refresh=False.
        if not refresh:
            logger.info(f"Cache stale ({int(age)}s old). Returning {len(cache_entry['activities'])} activities immediately.")
            return cache_entry["activities"]
    
    # Fetch all activities with pagination
    # Use lock to prevent concurrent full-history fetches for the same athlete
    async with ATHLETE_LOCKS[athlete_id]:
        # Double-check cache after acquiring lock!
        if athlete_id in ACTIVITY_CACHE:
            cache_entry = ACTIVITY_CACHE[athlete_id]
            age = time.time() - cache_entry["fetched_at"]
            if age < CACHE_TTL_SECONDS and not refresh:
                logger.info(f"Returning {len(cache_entry['activities'])} cached activities for athlete {athlete_id} (acquired lock)")
                return cache_entry["activities"]
            
            # If we just want to read but cache is stale, return it anyway to avoid blocking
            if not refresh and "activities" in cache_entry:
                 logger.info(f"Cache stale but available. Returning {len(cache_entry['activities'])} activities for athlete {athlete_id} (acquired lock)")
                 return cache_entry["activities"]

        all_activities = []
        page = 1
        
        try:
            while True:
                params = {"per_page": 200, "page": page}
                logger.info(f"Fetching activities page {page}...")
                
                try:
                    activities = await make_strava_request(
                        f"{STRAVA_API_BASE_URL}/athlete/activities",
                        params=params, 
                        access_token=x_strava_token
                    )
                except HTTPException as e:
                    # Check if it's a rate limit error (429)
                    if e.status_code == 429:
                        logger.warning(f"Rate limit hit at page {page}. Pausing for 60 seconds...")
                        await asyncio.sleep(60) # Async sleep!
                        try:
                             # Retry once
                             activities = await make_strava_request(
                                f"{STRAVA_API_BASE_URL}/athlete/activities",
                                params=params,
                                access_token=x_strava_token
                             )
                        except Exception as retry_e:
                            logger.error(f"Retry failed: {retry_e}. Returning partial activities.")
                            break 
                    else:
                        logger.error(f"Error fetching page {page}: {e}. Returning partial activities.")
                        break
                except Exception as e:
                    logger.error(f"Unexpected error fetching page {page}: {e}")
                    break
                
                if not isinstance(activities, list) or not activities:
                    break
                    
                all_activities.extend(activities)
                logger.info(f"Fetched {len(activities)} activities (Total: {len(all_activities)})")
                
                if len(activities) < 200:
                    break
                    
                page += 1
                # Respect rate limits - pause slightly
                await asyncio.sleep(1)
                
        except Exception as outer_e:
            logger.error(f"Fatal error in pagination loop: {outer_e}")
            
        # Save to cache if we got results
        if all_activities:
            ACTIVITY_CACHE[athlete_id] = {
                "activities": all_activities,
                "fetched_at": time.time()
            }
            save_cache_to_disk()
            dates = [a.get("start_date", "") for a in all_activities]
            dates.sort()
            if dates:
                logger.info(f"Fetched {len(all_activities)} activities. Range: {dates[0]} to {dates[-1]}")
    
    logger.info(f"Fetched and cached {len(all_activities)} total activities for athlete {athlete_id}")
    
    # Background hydration DISABLED for multi-user quota fairness.
    # Activity details are now fetched only on-demand when a user queries for them.
    # try:
    #     if not HYDRATION_LOCK.locked():
    #          logger.info("Triggering automatic background hydration...")
    #          asyncio.create_task(hydrate_activities_background(x_strava_token))
    #     else:
    #          logger.info("Hydration already in progress.")
    # except Exception:
    #     pass # Don't block
        
    return all_activities

@app.get("/activities/all")
async def get_all_activities(x_strava_token: str = Header(..., alias="X-Strava-Token"), refresh: bool = False) -> List[Dict[str, Any]]:
    """Get ALL activities from Strava by paginating through all pages. Results are cached for 5 minutes."""
    return await _fetch_all_activities_logic(x_strava_token, refresh)


    

# Global lock for hydration to prevent concurrent runs
HYDRATION_LOCK = asyncio.Lock()

async def hydrate_activities_background(token: str):
    """Background task to fetch full details for activities."""
    
    async with HYDRATION_LOCK:
        logger.info("Starting background hydration of activity details...")
        
        # Re-read cache
        athlete_id = TOKEN_TO_ID_CACHE.get(token)
        if not athlete_id or athlete_id not in ACTIVITY_CACHE:
             # Try to fetch athlete ID if missing (should be cached by now)
             return

        activities = ACTIVITY_CACHE[athlete_id].get("activities", [])
        
        # Identify candidates: activities matching "Jessica" should be prioritized if we could search them?
        # But we can't search them. So we rely on the heuristic.
        # Candidates: missing 'description'
        # Smart Hydration: Filter for high-value activities
        # We only want to auto-hydrate "high value" activities to save API calls
        # High value = Run, Ride, Swim OR High Social Engagement
        def is_high_value(act):
            atype = act.get('type', 'Run') # Default to Run if missing
            kudos = act.get('kudos_count', 0)
            comments = act.get('comment_count', 0)
            
            # 1. Always hydrate primary sports
            if atype in ['Run', 'Ride', 'Swim', 'VirtualRun', 'VirtualRide']:
                return True
                
            # 2. Hydrate anything with social engagement (e.g. a Hike with friends)
            if kudos > 5 or comments > 0:
                return True
                
            # 3. Specific keywords
            name = act.get('name', '').lower()
            if any(w in name for w in ['race', 'marathon', 'ftp', 'test']):
                return True
            
            # Default: Ignore Walks, Hikes, Yoga, Weights to save API
            return False

        candidates = [
            a for a in activities 
            if ("description" not in a or a["description"] is None) 
            and is_high_value(a)
        ]
        
        if not candidates:
            return

        logger.info(f"Found {len(candidates)} high-value activities needing hydration.")
        
        # Priority Scoring & Window Filtering
        def priority_score(act):
            score = 0
            start_date_str = act.get('start_date', '')
            if not start_date_str: return (0, "")
            
            # 12-MONTH WINDOW CHECK
            try:
                # Strava usually uses ISO format "2023-10-31T01:02:03Z"
                act_date = datetime.fromisoformat(start_date_str.replace("Z", "+00:00"))
                # If older than 12 months, give it a massive penalty so it drops out of the candidate list or is processed last
                # Actually, according to plan, we should just SKIP these candidates entirely below.
                if (datetime.now(act_date.tzinfo) - act_date).days > 365:
                    return (-10000, start_date_str)
            except Exception:
                pass

            atype = act.get('type', '')
            kudos = act.get('kudos_count', 0)
            name = act.get('name', '').lower()

            # Tier 1: Recent (Last ~15 months)
            current_year = datetime.now().year
            if str(current_year) in start_date_str:
                score += 1000
            elif str(current_year - 1) in start_date_str:
                score += 500
                
            # Tier 2: Social
            if kudos > 10:
                score += 50
            if act.get('comment_count', 0) > 0:
                score += 100
            
            # Tier 3: Important keywords
            if any(w in name for w in ['race', 'marathon', '5k', '10k', 'pr', 'pb']):
                score += 200
            if 'angeles' in name:
                score += 100
                
            # Tier 4: Type Preference
            if atype == 'Run':
                score += 20
                
            return (score, start_date_str)
        
        candidates.sort(key=priority_score, reverse=True)
        # Filter out candidates outside the 12-month window (Score < 0)
        candidates = [c for c in candidates if priority_score(c)[0] >= 0]
        
        # We process candidates in a loop, but we RELEASE the lock during the network call
        # so that other requests (like specific hydration) can interleave.
        # We need to copy the list of candidates to iterate (since they are just refs)
        # but we need to re-check status before hydrating.
        
    logger.info(f"Starting hydration loop for {len(candidates)} candidates...")
    
    hydrated_count = 0
    
    for act in candidates:
        # DYNAMIC THROTTLING
        stats = rate_limiter.get_stats()
        used_15m = stats.get("15m_used", 0)
        limit_15m = stats.get("15m_limit", 80)
        used_daily = stats.get("daily_used", 0)
        limit_daily = stats.get("daily_limit", 800)
        
        # Priority check: If we are nearing the global or daily limit, slow down or stop
        # background hydration to leave room for live user queries.
        sleep_time = 2 
        
        # DAILY LIMIT PROTECTION
        if used_daily > (limit_daily * 0.95): # > 900 calls (was 720)
            logger.warning("Daily rate limit nearly reached. Stopping background hydration for the day.")
            break
            
        # 15-MINUTE THROTTLING
        if used_15m > (limit_15m * 0.9): # > 72 calls 
            logger.warning("Critical 15m rate limit usage. Pausing background hydration.")
            break
        elif used_15m > (limit_15m * 0.75): # > 60 calls
            logger.info("High 15m rate limit usage. Throttling background hydration (10s sleep).")
            sleep_time = 10
        elif used_15m > (limit_15m * 0.5): # > 40 calls
            logger.info("Moderate 15m rate limit usage. Throttling background hydration (5s sleep).")
            sleep_time = 5 


        # Check if already hydrated (by another process)
        if "description" in act and act["description"] is not None:
            continue

        act_id = act['id']
        try:
            # Rate limit safety sleep
            await asyncio.sleep(sleep_time)
            
            logger.info(f"Hydrating activity {act_id} ({act.get('name')}). Usage: {used_15m}/{limit_15m}")
            
            # NETWORK CALL - NO LOCK
            detail = await make_strava_request(
                    f"{STRAVA_API_BASE_URL}/activities/{act_id}",
                    access_token=token
            )
            
            # UPDATE CACHE - ACQUIRE LOCK
            async with HYDRATION_LOCK:
                # Update in place (act is a reference to the dict in ACTIVITY_CACHE)
                act['description'] = detail.get('description', '')
                act['private_note'] = detail.get('private_note', '')
                act['segment_efforts'] = detail.get('segment_efforts', [])
                act['similar_activities'] = detail.get('similar_activities')
                act['athlete_count'] = detail.get('athlete_count', 1)
                act['hydrated_at'] = time.time()
                
                hydrated_count += 1
                
                if hydrated_count % 5 == 0:
                    save_cache_to_disk()
                
        except HTTPException as he:
            if he.status_code == 429:
                logger.error("Rate limit 429 hit (server side). Stopping hydration.")
                break
            logger.error(f"Failed to hydrate {act_id}: {he}")
        except Exception as e:
            logger.error(f"Failed to hydrate {act_id}: {e}")
            
    async with HYDRATION_LOCK:
        save_cache_to_disk()
    logger.info(f"Hydration complete/paused. {hydrated_count} activities updated.")

@app.post("/activities/refresh")
async def refresh_activities(x_strava_token: str = Header(..., alias="X-Strava-Token"), background_tasks: BackgroundTasks = None):
    """Trigger a background refresh of the activity cache (list only, no hydration)."""
    
    async def _do_refresh(token):
        logger.info("Starting background activity refresh...")
        try:
            # 1. Fetch latest summary list
            await _fetch_all_activities_logic(token, refresh=True)
            logger.info("Background refresh complete.")
            
            # 2. Hydration DISABLED for multi-user quota fairness
            # Activity details are now fetched only on-demand when a user queries for them.
            # asyncio.create_task(hydrate_activities_background(token))

        except Exception as e:
            logger.error(f"Background refresh failed: {e}")

    if background_tasks:
        background_tasks.add_task(_do_refresh, x_strava_token)
        return {"message": "Refresh started in background"}
    else:
        # Fallback (shouldn't happen)
        await _do_refresh(x_strava_token)
        return {"message": "Refresh completed (synchronous fallback)"}

@app.post("/activities/hydrate_ids")
async def hydrate_specific_activities(
    payload: HydrationRequest,
    x_strava_token: str = Header(..., alias="X-Strava-Token"),
    background_tasks: BackgroundTasks = None
):
    """Hydrate a specific list of activity IDs immediately."""
    
    async def _do_specific_hydration(token, ids):
        athlete_id = TOKEN_TO_ID_CACHE.get(token)
        if not athlete_id or athlete_id not in ACTIVITY_CACHE:
             logger.warning("Cache miss during specific hydration.")
             return
             
        activities = ACTIVITY_CACHE[athlete_id].get("activities", [])
        # Map ID to Activity Object
        act_map = {a['id']: a for a in activities}
        
        target_acts = []
        for aid in ids:
            if aid in act_map:
                target_acts.append(act_map[aid])
                
        logger.info(f"Specific Hydration: Found {len(target_acts)} of {len(ids)} requested activities.")
        
        for act in target_acts:
            # Check if done?
            if "description" in act and act["description"] is not None:
                continue

            # Check Rate Limit (safety first)
            if not rate_limiter.can_request():
                 logger.error("Rate limit hit during specific hydration.")
                 break
                 
            try:
                # Network Call (No Lock)
                detail = await make_strava_request(
                        f"{STRAVA_API_BASE_URL}/activities/{act['id']}",
                        access_token=token
                )
                
                # Update (Lock)
                async with HYDRATION_LOCK:
                     act['description'] = detail.get('description', '')
                     act['private_note'] = detail.get('private_note', '')
                     act['segment_efforts'] = detail.get('segment_efforts', [])
                     act['similar_activities'] = detail.get('similar_activities')
                     act['athlete_count'] = detail.get('athlete_count', 1)
                     act['hydrated_at'] = time.time()
                
                # Sleep a tiny bit to be nice?
                await asyncio.sleep(0.5)
                
            except Exception as e:
                logger.error(f"Specific hydration failed for {act['id']}: {e}")
                
        # Save at end
        async with HYDRATION_LOCK:
             save_cache_to_disk()
             
    if background_tasks:
        background_tasks.add_task(_do_specific_hydration, x_strava_token, payload.ids)
        return {"message": f"Queued hydration for {len(payload.ids)} activities."}
    else:
        await _do_specific_hydration(x_strava_token, payload.ids)
        return {"message": "Completed specific hydration."}

@app.get("/activities/summary")
async def get_activities_summary(x_strava_token: str = Header(..., alias="X-Strava-Token")) -> Dict[str, Any]:
    """Get a summarized view of all activities for efficient AI queries. Returns aggregated data by year/month."""
    # Get all activities (will use cache if available)
    all_activities = await get_all_activities(x_strava_token)
    
    # Background hydration DISABLED for multi-user quota fairness.
    # try:
    #      asyncio.create_task(hydrate_activities_background(x_strava_token))
    # except Exception:
    #      pass
    
    # Group activities by year and month
    by_year: Dict[str, Dict[str, Any]] = {}
    activities_by_date: Dict[str, List[Dict[str, Any]]] = {}
    
    for activity in all_activities:
        # Parse date
        start_date = activity.get("start_date_local", activity.get("start_date", ""))
        if not start_date:
            continue
            
        date_obj = datetime.fromisoformat(start_date.replace("Z", "+00:00"))
        year = str(date_obj.year)
        month = f"{year}-{date_obj.month:02d}"
        date_key = date_obj.strftime("%Y-%m-%d")
        
        # Initialize year if needed
        if year not in by_year:
            by_year[year] = {
                "total_activities": 0,
                "total_distance_miles": 0,
                "total_elevation_feet": 0,
                "total_moving_time_seconds": 0,
                "by_type": {},
                "by_month": {}
            }
        
        # Initialize month if needed
        if month not in by_year[year]["by_month"]:
            by_year[year]["by_month"][month] = {
                "activities": 0,
                "distance_miles": 0,
                "elevation_feet": 0,
                "moving_time_seconds": 0,
                "by_type": {}
            }
        
        # Track activities by date (condensed format)
        if date_key not in activities_by_date:
            activities_by_date[date_key] = []
        
        activity_type = activity.get("sport_type", activity.get("type", "Unknown"))
        distance_miles = activity.get("distance", 0) / 1609.344 # Official meters per mile for precision
        elevation_feet = activity.get("total_elevation_gain", 0) * 3.28084
        moving_time = activity.get("moving_time", 0)
        
        activities_by_date[date_key].append({
            "id": activity.get("id"),
            "name": activity.get("name", ""),
            "type": activity_type,
            "distance_miles": round(distance_miles, 3),
            "elevation_feet": round(elevation_feet, 0),
            "moving_time_seconds": moving_time,
            "elapsed_time_seconds": activity.get("elapsed_time", 0),
            "elapsed_time_str": format_seconds_to_str(activity.get("elapsed_time", 0)),
            "start_time": start_date,
            "private_note": activity.get("private_note", ""),
            "description": activity.get("description", ""),
            "athlete_count": activity.get("athlete_count", 1),
            "route_match_count": activity.get("similar_activities", {}).get("effort_count", 0) if activity.get("similar_activities") else 0,
            "hydrated": activity.get("hydrated_at") is not None
        })
        
        # Update year totals
        by_year[year]["total_activities"] += 1
        by_year[year]["total_distance_miles"] += distance_miles
        by_year[year]["total_elevation_feet"] += elevation_feet
        by_year[year]["total_moving_time_seconds"] += moving_time
        
        # Update type counts (Yearly)
        if activity_type not in by_year[year]["by_type"]:
            by_year[year]["by_type"][activity_type] = {"count": 0, "distance_miles": 0}
        by_year[year]["by_type"][activity_type]["count"] += 1
        by_year[year]["by_type"][activity_type]["distance_miles"] += distance_miles
        
        # Update month totals
        by_year[year]["by_month"][month]["activities"] += 1
        by_year[year]["by_month"][month]["distance_miles"] += distance_miles
        by_year[year]["by_month"][month]["elevation_feet"] += elevation_feet
        by_year[year]["by_month"][month]["moving_time_seconds"] += moving_time
        
        # Update type counts (Monthly)
        if activity_type not in by_year[year]["by_month"][month]["by_type"]:
            by_year[year]["by_month"][month]["by_type"][activity_type] = {"count": 0, "distance_miles": 0}
        by_year[year]["by_month"][month]["by_type"][activity_type]["count"] += 1
        by_year[year]["by_month"][month]["by_type"][activity_type]["distance_miles"] += distance_miles
    
    # Round the totals
    for year_data in by_year.values():
        year_data["total_distance_miles"] = round(year_data["total_distance_miles"], 2)
        year_data["total_elevation_feet"] = round(year_data["total_elevation_feet"], 0)
        for type_data in year_data["by_type"].values():
            type_data["distance_miles"] = round(type_data["distance_miles"], 2)
        
        for month_data in year_data["by_month"].values():
            month_data["distance_miles"] = round(month_data["distance_miles"], 2)
            month_data["elevation_feet"] = round(month_data["elevation_feet"], 0)
            # Round monthly type totals
            for m_type_data in month_data["by_type"].values():
                m_type_data["distance_miles"] = round(m_type_data["distance_miles"], 2)
    
    return {
        "total_activities": len(all_activities),
        "by_year": by_year,
        "activities_by_date": activities_by_date,  # Full list for date queries
        "cache_info": f"Data cached at {datetime.now().isoformat()}"
    }

@app.get("/activities/{activity_id}")
async def get_activity(activity_id: int, x_strava_token: str = Header(..., alias="X-Strava-Token")) -> Dict[str, Any]:
    """Get detailed activity data from Strava, checking cache first."""
    # 1. Try Cache
    athlete_id = TOKEN_TO_ID_CACHE.get(x_strava_token)
    if athlete_id and athlete_id in ACTIVITY_CACHE:
        acts = ACTIVITY_CACHE[athlete_id].get("activities", [])
        # Find the activity in the cache
        for act in acts:
            if act.get('id') == activity_id:
                # Check if it has 'description' (sign of hydration)
                if 'description' in act and act['description'] is not None:
                    logger.info(f"Cache Hit for detailed activity {activity_id}")
                    return act
    
    # 2. Fetch from API
    logger.info(f"Cache Miss for detailed activity {activity_id}. Fetching from API.")
    detail = await make_strava_request(f"{STRAVA_API_BASE_URL}/activities/{activity_id}", access_token=x_strava_token)
    
    # 3. Update cache if possible
    if athlete_id and athlete_id in ACTIVITY_CACHE:
         acts = ACTIVITY_CACHE[athlete_id].get("activities", [])
         for i, act in enumerate(acts):
             if act.get('id') == activity_id:
                 ACTIVITY_CACHE[athlete_id]["activities"][i].update(detail)
                 ACTIVITY_CACHE[athlete_id]["activities"][i]["hydrated_at"] = time.time()
                 save_cache_to_disk()
                 break
                 
    return detail

@app.get("/segments/{segment_id}")
async def get_segment(segment_id: int, x_strava_token: str = Header(..., alias="X-Strava-Token")) -> Dict[str, Any]:
    """Get detailed segment data from Strava, with caching."""
    cache_entry = SEGMENT_CACHE.get(segment_id)
    now = time.time()
    
    if cache_entry and "details" in cache_entry and (now - cache_entry["fetched_at"]) < SEGMENT_TTL:
        logger.info(f"Segment Cache Hit: {segment_id}")
        return cache_entry["details"]
        
    logger.info(f"Segment Cache Miss: {segment_id}")
    details = await make_strava_request(f"{STRAVA_API_BASE_URL}/segments/{segment_id}", access_token=x_strava_token)
    
    if segment_id not in SEGMENT_CACHE:
        SEGMENT_CACHE[segment_id] = {"fetched_at": now}
    SEGMENT_CACHE[segment_id]["details"] = details
    SEGMENT_CACHE[segment_id]["fetched_at"] = now
    
    return details

@app.get("/segments/{segment_id}/efforts")
async def get_segment_efforts(segment_id: int, page: int = 1, per_page: int = 50, x_strava_token: str = Header(..., alias="X-Strava-Token")) -> List[Dict[str, Any]]:
    """Get all efforts for a segment for the authenticated athlete.
    
    NOTE: Caching disabled for efforts to support full pagination.
    The backend fetches ALL pages with per_page=200, so caching page 1 
    with a smaller per_page was causing incomplete results.
    """
    # CACHE DISABLED - was preventing full pagination
    # The issue: Cache stored page 1 with per_page=50, then when backend
    # requested per_page=200, it returned the cached 50-result subset,
    # preventing pagination from fetching all 183 efforts.
    
    efforts = await make_strava_request(
        f"{STRAVA_API_BASE_URL}/segment_efforts",
        params={"segment_id": segment_id, "page": page, "per_page": per_page},
        access_token=x_strava_token
    )
    
    return efforts

@app.get("/segments/{segment_id}/leaderboard")
async def get_segment_leaderboard(segment_id: int, gender: Optional[str] = None, weight_class: Optional[str] = None, x_strava_token: str = Header(..., alias="X-Strava-Token")) -> Dict[str, Any]:
    """Get the leaderboard for a segment, with caching."""
    # Only cache default leaderboard (no filters)
    if not gender and not weight_class:
        cache_entry = SEGMENT_CACHE.get(segment_id)
        now = time.time()
        if cache_entry and "leaderboard" in cache_entry and (now - cache_entry.get("lb_fetched_at", 0)) < SEGMENT_EFFORTS_TTL:
             logger.info(f"Segment Leaderboard Cache Hit: {segment_id}")
             return cache_entry["leaderboard"]

    params = {"per_page": 5} # Top 5 is usually enough for CR
    if gender:
        params["gender"] = gender
    if weight_class:
        params["weight_class"] = weight_class
        
    lb = await make_strava_request(
        f"{STRAVA_API_BASE_URL}/segments/{segment_id}/leaderboard",
        params=params,
        access_token=x_strava_token
    )
    
    if not gender and not weight_class:
        if segment_id not in SEGMENT_CACHE:
            SEGMENT_CACHE[segment_id] = {}
        SEGMENT_CACHE[segment_id]["leaderboard"] = lb
        SEGMENT_CACHE[segment_id]["lb_fetched_at"] = time.time()
        
    return lb

@app.get("/segments/starred")
async def get_starred_segments(page: int = 1, per_page: int = 50, x_strava_token: str = Header(..., alias="X-Strava-Token")) -> List[Dict[str, Any]]:
    """Get the authenticated athlete's starred segments with caching."""
    athlete_id = TOKEN_TO_ID_CACHE.get(x_strava_token)
    
    # Simple global cache for starred segments (per athlete)
    # We use a special key in the athlete cache for this
    if athlete_id and athlete_id in ACTIVITY_CACHE:
        cache = ACTIVITY_CACHE[athlete_id]
        now = time.time()
        if "starred_segments" in cache and (now - cache.get("starred_fetched_at", 0)) < STARRED_SEGMENTS_TTL:
            logger.info(f"Returning cached starred segments for athlete {athlete_id}")
            return cache["starred_segments"]

    starred = await make_strava_request(
        f"{STRAVA_API_BASE_URL}/segments/starred",
        params={"page": page, "per_page": per_page},
        access_token=x_strava_token
    )
    
    if athlete_id and athlete_id in ACTIVITY_CACHE:
        ACTIVITY_CACHE[athlete_id]["starred_segments"] = starred
        ACTIVITY_CACHE[athlete_id]["starred_fetched_at"] = time.time()
        save_cache_to_disk()
        
    return starred

@app.get("/athlete/stats")
async def get_athlete_stats(x_strava_token: str = Header(..., alias="X-Strava-Token"), background_tasks: BackgroundTasks = None) -> Dict[str, Any]:
    """Get athlete statistics from Strava."""
    global ACTIVITY_CACHE, TOKEN_TO_ID_CACHE, LAST_HYDRATION_TRIGGER

    # 1. Get Athlete ID (Cached)
    athlete_id = TOKEN_TO_ID_CACHE.get(x_strava_token)
    if not athlete_id:
        # Fetch if not in memory cache
        athlete = await make_strava_request(f"{STRAVA_API_BASE_URL}/athlete", access_token=x_strava_token)
        athlete_id = str(athlete["id"])
        TOKEN_TO_ID_CACHE[x_strava_token] = athlete_id
    
    # 2. Check Cache for Stats
    current_time = time.time()
    
    # Helper to inject live app status
    def inject_app_status(stats_dict):
        if athlete_id in ACTIVITY_CACHE:
             acts = ACTIVITY_CACHE[athlete_id].get("activities", [])
             total = len(acts)
             hydrated = sum(1 for a in acts if a.get("hydrated_at"))
             percent = round(hydrated / total * 100 if total else 0, 1)
             stats_dict["app_status"] = {
                 "synced_activities": total,
                 "enriched_activities": hydrated,
                 "percent": percent
             }
             
             # AUTO-TRIGGER HYDRATION CHECK (DISABLED for multi-user quota fairness)
             # Background hydration consumed the entire 15m quota, blocking interactive queries.
             # Activity details are now fetched on-demand only when a user queries for them.
             # To re-enable, uncomment the block below.
             #
             # global LAST_HYDRATION_TRIGGER
             # if percent < 100 and (time.time() - LAST_HYDRATION_TRIGGER) > 300:
             #     if background_tasks:
             #         logger.info(f"Auto-triggering background hydration (Progress: {percent}%)")
             #         background_tasks.add_task(hydrate_activities_background, x_strava_token)
             #         LAST_HYDRATION_TRIGGER = time.time()
             #     else:
             #         logger.warning("Cannot auto-trigger hydration: BackgroundTasks not available")

        return stats_dict

    if athlete_id in ACTIVITY_CACHE:
        cache_entry = ACTIVITY_CACHE[athlete_id]
        stats = cache_entry.get("stats")
        stats_fetched_at = cache_entry.get("stats_fetched_at", 0)
        
        # 15 minute TTL for stats
        if stats and (current_time - stats_fetched_at) < 900:
            logger.info(f"Returning cached stats for athlete {athlete_id}")
            return inject_app_status(stats)

    # 3. Fetch from API
    stats_data = await make_strava_request(f"{STRAVA_API_BASE_URL}/athletes/{athlete_id}/stats", access_token=x_strava_token)
    
    # 4. Save to Cache
    if athlete_id not in ACTIVITY_CACHE:
        ACTIVITY_CACHE[athlete_id] = {}
    
    ACTIVITY_CACHE[athlete_id]["stats"] = stats_data
    ACTIVITY_CACHE[athlete_id]["stats_fetched_at"] = current_time
    save_cache_to_disk()
    
    return inject_app_status(stats_data)

@app.get("/gear/{gear_id}")
async def get_gear(gear_id: str, x_strava_token: str = Header(..., alias="X-Strava-Token")) -> Dict[str, Any]:
    """Get details for a piece of gear (shoe/bike)."""
    return await make_strava_request(f"{STRAVA_API_BASE_URL}/gear/{gear_id}", access_token=x_strava_token)

@app.get("/activities/{activity_id}/zones")
async def get_activity_zones(activity_id: int, x_strava_token: str = Header(..., alias="X-Strava-Token")) -> List[Dict[str, Any]]:
    """Get heart rate and power zones for an activity."""
    return await make_strava_request(f"{STRAVA_API_BASE_URL}/activities/{activity_id}/zones", access_token=x_strava_token)

@app.get("/clubs")
async def get_clubs(x_strava_token: str = Header(..., alias="X-Strava-Token")) -> List[Dict[str, Any]]:
    """List the authenticated athlete's clubs."""
    return await make_strava_request(f"{STRAVA_API_BASE_URL}/athlete/clubs", access_token=x_strava_token)

@app.get("/routes")
async def get_routes(x_strava_token: str = Header(..., alias="X-Strava-Token"), limit: int = 50) -> List[Dict[str, Any]]:
    """List the authenticated athlete's created routes."""
    return await make_strava_request(f"{STRAVA_API_BASE_URL}/athlete/routes", params={"per_page": limit}, access_token=x_strava_token)

@app.get("/routes/{route_id}/export_gpx")
async def get_route_gpx(route_id: int, x_strava_token: str = Header(..., alias="X-Strava-Token")):
    """Download the GPX file for a route."""
    gpx_content = await make_strava_request(
        f"{STRAVA_API_BASE_URL}/routes/{route_id}/export_gpx", 
        access_token=x_strava_token,
        response_type="content"
    )
    
    return Response(
        content=gpx_content,
        media_type="application/gpx+xml",
        headers={"Content-Disposition": f"attachment; filename=route_{route_id}.gpx"}
    )

@app.get("/activities/{activity_id}/map")
async def get_activity_with_map(activity_id: int, format: str = 'html', x_strava_token: str = Header(..., alias="X-Strava-Token")) -> Response:
    """Get detailed activity data from Strava with map visualization."""
    try:
        activity_data = await make_strava_request(f"{STRAVA_API_BASE_URL}/activities/{activity_id}", access_token=x_strava_token)
        logger.debug(f"Retrieved activity data for ID {activity_id}")
        
        # Note: formatting is CPU bound, could be offloaded to threadpool if needed
        # but for text/html generation it's usually fast enough.
        formatted_activity = format_activity_with_map(activity_data, format)
        logger.debug(f"Formatted activity data with {format} format")
        
        if format == 'html':
            return HTMLResponse(content=formatted_activity, media_type="text/html")
        else:
            return {
                "formatted_activity": formatted_activity,
                "activity": activity_data
            }
    except Exception as e:
        logger.error(f"Error processing activity {activity_id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

# ============================================================================
# ADDITIONAL ENDPOINTS FOR FEATURE PARITY
# ============================================================================

@app.get("/activities/{activity_id}/streams")
async def get_activity_streams(
    activity_id: int, 
    keys: str = "time,distance,latlng,altitude,velocity_smooth,heartrate,cadence,watts,temp,moving,grade_smooth",
    x_strava_token: str = Header(..., alias="X-Strava-Token")
) -> Dict[str, Any]:
    """Get activity streams (time-series data like heart rate, GPS, etc)."""
    return await make_strava_request(
        f"{STRAVA_API_BASE_URL}/activities/{activity_id}/streams",
        params={"keys": keys, "key_by_type": True},
        access_token=x_strava_token
    )

@app.get("/activities/{activity_id}/laps")
async def get_activity_laps(activity_id: int, x_strava_token: str = Header(..., alias="X-Strava-Token")) -> List[Dict[str, Any]]:
    """Get laps for an activity."""
    return await make_strava_request(f"{STRAVA_API_BASE_URL}/activities/{activity_id}/laps", access_token=x_strava_token)

@app.get("/activities/{activity_id}/comments")
async def get_activity_comments(
    activity_id: int, page: int = 1, per_page: int = 30,
    x_strava_token: str = Header(..., alias="X-Strava-Token")
) -> List[Dict[str, Any]]:
    """Get comments for an activity."""
    return await make_strava_request(
        f"{STRAVA_API_BASE_URL}/activities/{activity_id}/comments",
        params={"page": page, "per_page": per_page},
        access_token=x_strava_token
    )

@app.get("/activities/{activity_id}/kudos")
async def get_activity_kudoers(
    activity_id: int, page: int = 1, per_page: int = 30,
    x_strava_token: str = Header(..., alias="X-Strava-Token")
) -> List[Dict[str, Any]]:
    """Get kudoers for an activity."""
    return await make_strava_request(
        f"{STRAVA_API_BASE_URL}/activities/{activity_id}/kudos",
        params={"page": page, "per_page": per_page},
        access_token=x_strava_token
    )

@app.get("/athlete/zones")
async def get_athlete_zones(x_strava_token: str = Header(..., alias="X-Strava-Token")) -> Dict[str, Any]:
    """Get athlete heart rate and power zones."""
    return await make_strava_request(f"{STRAVA_API_BASE_URL}/athlete/zones", access_token=x_strava_token)

@app.get("/clubs/{club_id}")
async def get_club(club_id: int, x_strava_token: str = Header(..., alias="X-Strava-Token")) -> Dict[str, Any]:
    """Get club details."""
    return await make_strava_request(f"{STRAVA_API_BASE_URL}/clubs/{club_id}", access_token=x_strava_token)

@app.get("/clubs/{club_id}/activities")
async def get_club_activities(
    club_id: int, page: int = 1, per_page: int = 30,
    x_strava_token: str = Header(..., alias="X-Strava-Token")
) -> List[Dict[str, Any]]:
    """Get activities from a club."""
    return await make_strava_request(
        f"{STRAVA_API_BASE_URL}/clubs/{club_id}/activities",
        params={"page": page, "per_page": per_page},
        access_token=x_strava_token
    )

@app.get("/clubs/{club_id}/members")
async def get_club_members(
    club_id: int, page: int = 1, per_page: int = 30,
    x_strava_token: str = Header(..., alias="X-Strava-Token")
) -> List[Dict[str, Any]]:
    """Get members of a club."""
    return await make_strava_request(
        f"{STRAVA_API_BASE_URL}/clubs/{club_id}/members",
        params={"page": page, "per_page": per_page},
        access_token=x_strava_token
    )

@app.get("/clubs/{club_id}/admins")
async def get_club_admins(
    club_id: int, page: int = 1, per_page: int = 30,
    x_strava_token: str = Header(..., alias="X-Strava-Token")
) -> List[Dict[str, Any]]:
    """Get administrators of a club."""
    return await make_strava_request(
        f"{STRAVA_API_BASE_URL}/clubs/{club_id}/admins",
        params={"page": page, "per_page": per_page},
        access_token=x_strava_token
    )

@app.get("/routes/{route_id}")
async def get_route(route_id: int, x_strava_token: str = Header(..., alias="X-Strava-Token")) -> Dict[str, Any]:
    """Get route details."""
    return await make_strava_request(f"{STRAVA_API_BASE_URL}/routes/{route_id}", access_token=x_strava_token)

@app.get("/routes/{route_id}/streams")
async def get_route_streams(route_id: int, x_strava_token: str = Header(..., alias="X-Strava-Token")) -> List[Dict[str, Any]]:
    """Get route streams (GPS coordinates, elevation, etc)."""
    return await make_strava_request(f"{STRAVA_API_BASE_URL}/routes/{route_id}/streams", access_token=x_strava_token)

@app.get("/routes/{route_id}/export_tcx")
async def get_route_tcx(route_id: int, x_strava_token: str = Header(..., alias="X-Strava-Token")):
    """Export route as TCX file."""
    tcx_content = await make_strava_request(
        f"{STRAVA_API_BASE_URL}/routes/{route_id}/export_tcx",
        access_token=x_strava_token,
        response_type="content"
    )
    return Response(
        content=tcx_content,
        media_type="application/vnd.garmin.tcx+xml",
        headers={"Content-Disposition": f"attachment; filename=route_{route_id}.tcx"}
    )

@app.get("/segments/{segment_id}/streams")
async def get_segment_streams(
    segment_id: int,
    keys: str = "distance,latlng,altitude",
    x_strava_token: str = Header(..., alias="X-Strava-Token")
) -> List[Dict[str, Any]]:
    """Get segment streams."""
    return await make_strava_request(
        f"{STRAVA_API_BASE_URL}/segments/{segment_id}/streams",
        params={"keys": keys, "key_by_type": True},
        access_token=x_strava_token
    )

@app.get("/segment_efforts/{effort_id}/streams")
async def get_segment_effort_streams(
    effort_id: int,
    keys: str = "distance,latlng,altitude,velocity_smooth,heartrate,cadence,watts,grade_smooth,moving",
    x_strava_token: str = Header(..., alias="X-Strava-Token")
) -> List[Dict[str, Any]]:
    """Get segment effort streams."""
    return await make_strava_request(
        f"{STRAVA_API_BASE_URL}/segment_efforts/{effort_id}/streams",
        params={"keys": keys, "key_by_type": True},
        access_token=x_strava_token
    )

@app.put("/segments/{segment_id}/starred")
async def star_segment(
    segment_id: int,
    starred: bool = True,
    x_strava_token: str = Header(..., alias="X-Strava-Token")
) -> Dict[str, Any]:
    """Star or unstar a segment."""
    return await make_strava_request(
        f"{STRAVA_API_BASE_URL}/segments/{segment_id}/starred",
        method="PUT",
        params={"starred": starred},
        access_token=x_strava_token
    )

@app.post("/activities")
async def create_activity(
    name: str,
    sport_type: str,
    start_date_local: str,
    elapsed_time: int,
    description: str = "",
    distance: float = 0,
    trainer: int = 0,
    commute: int = 0,
    x_strava_token: str = Header(..., alias="X-Strava-Token")
) -> Dict[str, Any]:
    """Create a manual activity."""
    return await make_strava_request(
        f"{STRAVA_API_BASE_URL}/activities",
        method="POST",
        params={
            "name": name,
            "sport_type": sport_type,
            "start_date_local": start_date_local,
            "elapsed_time": elapsed_time,
            "description": description,
            "distance": distance,
            "trainer": trainer,
            "commute": commute
        },
        access_token=x_strava_token
    )

@app.put("/activities/{activity_id}")
async def update_activity(
    activity_id: int,
    name: Optional[str] = None,
    sport_type: Optional[str] = None,
    description: Optional[str] = None,
    trainer: Optional[int] = None,
    commute: Optional[int] = None,
    x_strava_token: str = Header(..., alias="X-Strava-Token")
) -> Dict[str, Any]:
    """Update an existing activity."""
    params = {k: v for k, v in {
        "name": name, "sport_type": sport_type, "description": description,
        "trainer": trainer, "commute": commute
    }.items() if v is not None}
    return await make_strava_request(
        f"{STRAVA_API_BASE_URL}/activities/{activity_id}",
        method="PUT",
        params=params,
        access_token=x_strava_token
    )

@app.put("/athlete")
async def update_athlete(
    weight: Optional[float] = None,
    x_strava_token: str = Header(..., alias="X-Strava-Token")
) -> Dict[str, Any]:
    """Update athlete information."""
    params = {}
    if weight is not None:
        params["weight"] = weight
    return await make_strava_request(
        f"{STRAVA_API_BASE_URL}/athlete",
        method="PUT",
        params=params,
        access_token=x_strava_token
    )

def main() -> None:
    """Main entry point for the server."""
    try:
        logger.info("Starting Strava HTTP Server...")
        uvicorn.run(app, host="0.0.0.0", port=8001)
    except Exception as e:
        logger.error(f"Server error: {str(e)}", exc_info=True)
        raise

if __name__ == "__main__":
    main() 