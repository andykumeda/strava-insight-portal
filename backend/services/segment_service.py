import logging
import httpx
import os
from typing import List
from sqlalchemy.orm import Session
from ..models import Segment, SegmentEffort

logger = logging.getLogger(__name__)

async def sync_starred_segments(token: str, db: Session):
    """
    Fetch starred segments from Strava (via MCP) and save to local DB.
    This enables fuzzy-matching of segment names for users.
    """
    mcp_url = os.getenv("MCP_SERVER_URL", "http://localhost:8001")
    logger.info("Syncing starred segments...")
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            resp = await client.get(
                f"{mcp_url}/segments/starred",
                headers={"X-Strava-Token": token}
            )
            if resp.status_code != 200:
                logger.error(f"Failed to fetch starred segments: {resp.text}")
                return
            
            starred = resp.json()
            logger.info(f"Found {len(starred)} starred segments.")
            
            for sdata in starred:
                # Upsert Segment
                sid = sdata.get("id")
                segment = db.query(Segment).filter(Segment.id == sid).first()
                if not segment:
                    segment = Segment(
                        id=sid,
                        name=sdata.get("name"),
                        distance=sdata.get("distance"),
                        average_grade=sdata.get("average_grade"),
                        city=sdata.get("city")
                    )
                    db.add(segment)
                else:
                    segment.name = sdata.get("name")
                    segment.distance = sdata.get("distance")
                    segment.average_grade = sdata.get("average_grade")
                    segment.city = sdata.get("city")
            
            db.commit()
            logger.info("Starred segments synced successfully.")
        except Exception as e:
            logger.error(f"Error syncing starred segments: {e}")
            db.rollback()

def save_segments_from_activity(activity_data: dict, db: Session):
    if "segment_efforts" not in activity_data:
        return
    
    segment_efforts = activity_data["segment_efforts"]
    """
    Extracts segment efforts from detailed activity data and saves/updates them in the database.
    """
    activity_id = activity_data.get("id")
    segment_efforts = activity_data.get("segment_efforts", [])

    if not activity_id or not segment_efforts:
        return

    logger.info(f"Processing {len(segment_efforts)} segment efforts for activity {activity_id}")

    for effort in segment_efforts:
        segment_data = effort.get("segment")
        if not segment_data:
            continue

        # 1. Upsert Segment
        segment_id = segment_data.get("id")
        segment = db.query(Segment).filter(Segment.id == segment_id).first()
        
        if not segment:
            segment = Segment(
                id=segment_id,
                name=segment_data.get("name"),
                distance=segment_data.get("distance"),
                average_grade=segment_data.get("average_grade"),
                city=segment_data.get("city")
            )
            db.add(segment)
        else:
            # Update fields if needed (e.g. name change)
            segment.name = segment_data.get("name")
            segment.distance = segment_data.get("distance")
            segment.average_grade = segment_data.get("average_grade")
            segment.city = segment_data.get("city")
        
        # Flush to ensure segment exists for FK
        db.flush()

        # 2. Upsert Segment Effort
        effort_id = effort.get("id")
        segment_effort = db.query(SegmentEffort).filter(SegmentEffort.id == effort_id).first()

        # Parse date
        start_date_str = effort.get("start_date")
        from datetime import datetime
        start_date = datetime.fromisoformat(start_date_str.replace("Z", "+00:00")) if start_date_str else None

        if not segment_effort:
            segment_effort = SegmentEffort(
                id=effort_id,
                segment_id=segment_id,
                activity_id=activity_id,
                elapsed_time=effort.get("elapsed_time"),
                moving_time=effort.get("moving_time"),
                start_date=start_date,
                kom_rank=effort.get("kom_rank"),
                pr_rank=effort.get("pr_rank")
            )
            db.add(segment_effort)
        else:
            segment_effort.elapsed_time = effort.get("elapsed_time")
            segment_effort.moving_time = effort.get("moving_time")
            segment_effort.kom_rank = effort.get("kom_rank")
            segment_effort.pr_rank = effort.get("pr_rank")
    
    try:
        db.commit()
    except Exception as e:
        logger.error(f"Error saving segments: {e}")
        db.rollback()

def search_segments(query: str, db: Session, limit: int = 5) -> List[Segment]:
    """
    Fuzzy search for segments by name.
    """
    # Simple ILIKE search for now
    search_term = f"%{query}%"
    return db.query(Segment).filter(Segment.name.ilike(search_term)).limit(limit).all()

def get_best_efforts_for_segment(segment_id: int, db: Session, limit: int = 3) -> List[SegmentEffort]:
    """
    Get top efforts for a segment (ordered by elapsed time).
    """
    return db.query(SegmentEffort)\
        .filter(SegmentEffort.segment_id == segment_id)\
        .order_by(SegmentEffort.elapsed_time.asc())\
        .limit(limit)\
        .all()
