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
from datetime import datetime
import logging
from fastapi import FastAPI, HTTPException, Response, Header, BackgroundTasks
from fastapi.responses import HTMLResponse
import uvicorn
import httpx
from map_utils import format_activity_with_map

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

CACHE_TTL_SECONDS = 300  # 5 minutes

# Create FastAPI app
app = FastAPI(
    title="Strava API Server",
    description="HTTP server for Strava API integration",
)

async def make_strava_request(url: str, method: str = "GET", params: Dict[str, Any] = None, access_token: str = None) -> Any:
    """Make a request to the Strava API."""
    if not access_token:
        raise HTTPException(status_code=401, detail="Missing X-Strava-Token header")
    
    headers = {"Authorization": f"Bearer {access_token}"}
    
    # Retry logic for 429s (Rate Limit Exceeded)
    max_retries = 3
    retry_count = 0
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        while True:
            try:
                response = await client.request(
                    method=method,
                    url=url,
                    headers=headers,
                    params=params
                )
                
                # Check for 429 Rate Limit
                if response.status_code == 429:
                    if retry_count < max_retries:
                        retry_count += 1
                        # Get wait time from header or default to exponential backoff
                        retry_after = int(response.headers.get("Retry-After", 15))
                        # Cap potential wait time (don't wait too long)
                        wait_time = min(retry_after, 60)
                        
                        logger.warning(f"Rate limited (429). Waiting {wait_time}s then retrying ({retry_count}/{max_retries})...")
                        await asyncio.sleep(wait_time)
                        continue
                    else:
                        logger.error("Rate limit retry count exceeded.")
                        raise HTTPException(status_code=429, detail="Rate limit exceeded")

                if response.status_code == 401:
                     raise HTTPException(status_code=401, detail="Invalid or expired Strava token")
                
                response.raise_for_status()
                return response.json()
                
            except httpx.HTTPStatusError as e:
                logger.error(f"Strava API request failed: {str(e)}")
                raise HTTPException(status_code=e.response.status_code, detail=str(e))
            except httpx.RequestError as e:
                logger.error(f"Strava API connection error: {str(e)}")
                raise HTTPException(status_code=500, detail=f"Connection error: {str(e)}")
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
        dates = [a.get("start_date", "") for a in all_activities]
        dates.sort()
        if dates:
            logger.info(f"Fetched {len(all_activities)} activities. Range: {dates[0]} to {dates[-1]}")
    
    logger.info(f"Fetched and cached {len(all_activities)} total activities for athlete {athlete_id}")
    return all_activities

@app.get("/activities/all")
async def get_all_activities(x_strava_token: str = Header(..., alias="X-Strava-Token"), refresh: bool = False) -> List[Dict[str, Any]]:
    """Get ALL activities from Strava by paginating through all pages. Results are cached for 5 minutes."""
    return await _fetch_all_activities_logic(x_strava_token, refresh)

@app.post("/activities/refresh")
async def refresh_activities(x_strava_token: str = Header(..., alias="X-Strava-Token"), background_tasks: BackgroundTasks = None):
    """Trigger a background refresh of the activity cache."""
    
    async def _do_refresh(token):
        logger.info("Starting background activity refresh...")
        try:
            await _fetch_all_activities_logic(token, refresh=True)
            logger.info("Background refresh complete.")
        except Exception as e:
            logger.error(f"Background refresh failed: {e}")

    if background_tasks:
        background_tasks.add_task(_do_refresh, x_strava_token)
        return {"message": "Refresh started in background"}
    else:
        # Fallback (shouldn't happen)
        await _do_refresh(x_strava_token)
        return {"message": "Refresh completed (synchronous fallback)"}

@app.get("/activities/summary")
async def get_activities_summary(x_strava_token: str = Header(..., alias="X-Strava-Token")) -> Dict[str, Any]:
    """Get a summarized view of all activities for efficient AI queries. Returns aggregated data by year/month."""
    # Get all activities (will use cache if available)
    all_activities = await get_all_activities(x_strava_token)
    
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
        distance_miles = activity.get("distance", 0) * 0.000621371
        elevation_feet = activity.get("total_elevation_gain", 0) * 3.28084
        moving_time = activity.get("moving_time", 0)
        
        activities_by_date[date_key].append({
            "name": activity.get("name", ""),
            "type": activity_type,
            "distance_miles": round(distance_miles, 2),
            "elevation_feet": round(elevation_feet, 0),
            "moving_time_seconds": moving_time,
            "start_time": start_date,
            "private_note": activity.get("private_note", ""),
            "description": activity.get("description", "")
        })
        
    # Debug log to check if private notes are being captured
    if all_activities:
        has_notes = sum(1 for a in all_activities if a.get("private_note"))
        has_desc = sum(1 for a in all_activities if a.get("description"))
        logger.info(f"Summary generation: {has_notes} notes, {has_desc} descriptions out of {len(all_activities)}")
        
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
    """Get detailed activity data from Strava."""
    return await make_strava_request(f"{STRAVA_API_BASE_URL}/activities/{activity_id}", access_token=x_strava_token)

@app.get("/athlete/stats")
async def get_athlete_stats(x_strava_token: str = Header(..., alias="X-Strava-Token")) -> Dict[str, Any]:
    """Get athlete statistics from Strava."""
    # First get athlete ID
    athlete = await make_strava_request(f"{STRAVA_API_BASE_URL}/athlete", access_token=x_strava_token)
    athlete_id = athlete["id"]
    
    # Then get stats
    return await make_strava_request(f"{STRAVA_API_BASE_URL}/athletes/{athlete_id}/stats", access_token=x_strava_token)

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