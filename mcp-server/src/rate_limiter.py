import time
import json
import os
import logging
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
    
    # Safety Limits
    LIMIT_15_MIN = 80
    LIMIT_DAILY = 950
    
    def __init__(self):
        self.requests_15m: List[float] = []
        self.requests_daily: List[float] = []
        self._load_state()

    def _load_state(self):
        """Load request timestamps from disk."""
        if os.path.exists(self.STATE_FILE):
            try:
                with open(self.STATE_FILE, 'r') as f:
                    data = json.load(f)
                    self.requests_15m = data.get('15m', [])
                    self.requests_daily = data.get('daily', [])
                self._cleanup()
                logger.info(f"Rate Limiter loaded. Used: {len(self.requests_15m)}/15m, {len(self.requests_daily)}/day")
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
        # 15 minutes = 900 seconds
        self.requests_15m = [t for t in self.requests_15m if now - t < 900]
        # 1 day = 86400 seconds
        self.requests_daily = [t for t in self.requests_daily if now - t < 86400]

    def can_request(self) -> bool:
        """Check if a request is allowed. Does NOT record the request."""
        self._cleanup()
        if len(self.requests_15m) >= self.LIMIT_15_MIN:
            logger.warning(f"Rate Limit Hit (15m): {len(self.requests_15m)}/{self.LIMIT_15_MIN}")
            return False
        if len(self.requests_daily) >= self.LIMIT_DAILY:
            logger.warning(f"Rate Limit Hit (Daily): {len(self.requests_daily)}/{self.LIMIT_DAILY}")
            return False
        return True

    def record_request(self):
        """Record a successful request."""
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
