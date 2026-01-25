import time
import json
import os
import logging
from datetime import datetime, timezone
from typing import List, Dict

logger = logging.getLogger(__name__)

class StravaRateLimiter:
    """
    Persistent Rate Limiter for Strava API.
    Enforces strict limits to avoid 429s and bans.
    
    Strava Limits:
    - 100 requests every 15 minutes
    - 1000 requests every day
    
    Our Safety Limits (80% capacity):
    - 80 requests every 15 minutes
    - 800 requests every day
    """
    
    STATE_FILE = "rate_limit_state.json"
    
    # Safety Limits (Stricter: 80% of 100/15m and 80% of 1000/day)
    LIMIT_15_MIN = 80
    LIMIT_DAILY = 800
    
    def __init__(self):
        self.requests_15m: List[float] = []
        self.requests_daily: List[float] = []
        self._load_state()

    def _load_state(self):
        """Load request timestamps from disk."""
        if os.path.exists(self.STATE_FILE):
            try:
                # Use simple file reading for speed; assuming single instance or sporadic access
                with open(self.STATE_FILE, 'r') as f:
                    data = json.load(f)
                    self.requests_15m = data.get('15m', [])
                    self.requests_daily = data.get('daily', [])
                self._cleanup()
            except Exception as e:
                logger.error(f"Failed to load rate limit state: {e}")
                
    def _save_state(self):
        """Save request timestamps to disk."""
        try:
            with open(self.STATE_FILE, 'w') as f:
                json.dump({
                    '15m': self.requests_15m,
                    'daily': self.requests_daily
                }, f)
        except Exception as e:
            logger.error(f"Failed to save rate limit state: {e}")

    def _cleanup(self):
        """Remove timestamps older than the windows."""
        now = time.time()
        # 15 minutes = 900 seconds (sliding window is fine for 15m safety)
        self.requests_15m = [t for t in self.requests_15m if now - t < 900]
        
        # Daily Limit: Strava resets at midnight UTC.
        # We only keep timestamps from the CURRENT UTC day.
        today_utc = datetime.now(timezone.utc).date()
        self.requests_daily = [
            t for t in self.requests_daily 
            if datetime.fromtimestamp(t, timezone.utc).date() == today_utc
        ]


    def can_request(self) -> bool:
        """Check if a request is allowed. Reloads state from disk first."""
        self._load_state() 
        if len(self.requests_15m) >= self.LIMIT_15_MIN:
            logger.warning(f"Rate Limit Hit (15m): {len(self.requests_15m)}/{self.LIMIT_15_MIN}")
            return False
        if len(self.requests_daily) >= self.LIMIT_DAILY:
            logger.warning(f"Rate Limit Hit (Daily): {len(self.requests_daily)}/{self.LIMIT_DAILY}")
            return False
        return True

    def record_attempt(self):
        """Record a request ATTEMPT (call this BEFORE the HTTP request)."""
        self._load_state()
        now = time.time()
        self.requests_15m.append(now)
        self.requests_daily.append(now)
        self._save_state()
        
    def get_stats(self) -> Dict[str, int]:
        self._cleanup()
        return {
            "15m_used": len(self.requests_15m),
            "15m_limit": self.LIMIT_15_MIN,
            "daily_used": len(self.requests_daily),
            "daily_limit": self.LIMIT_DAILY
        }

# Global Instance
rate_limiter = StravaRateLimiter()
