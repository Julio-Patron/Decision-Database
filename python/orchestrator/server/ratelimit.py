import time
import logging
from typing import Dict, Tuple, Optional

logger = logging.getLogger(__name__)

class RateLimiter:
    """
    Token bucket rate limiter.
    Limits by API key and IP address.
    """
    def __init__(self, requests_per_minute: int = 120, burst_limit: int = 100):
        self.requests_per_minute = requests_per_minute
        self.burst_limit = burst_limit
        self._tokens: Dict[str, Tuple[float, float]] = {}
        self._last_cleanup = time.time()

    def _cleanup_old_tokens(self, now: float) -> None:
        if now - self._last_cleanup < 60:
            return
        self._last_cleanup = now
        # Remove tokens that have fully replenished
        keys_to_delete = []
        rate = self.requests_per_minute / 60.0
        for k, (last_time, tokens) in self._tokens.items():
            if tokens + (now - last_time) * rate >= self.burst_limit:
                keys_to_delete.append(k)
        for k in keys_to_delete:
            del self._tokens[k]

    def is_allowed(self, key: str, weight: int = 1) -> bool:
        now = time.time()
        self._cleanup_old_tokens(now)
        rate = self.requests_per_minute / 60.0

        if key not in self._tokens:
            if weight > self.burst_limit:
                return False
            self._tokens[key] = (now, float(self.burst_limit - weight))
            return True

        last_time, tokens = self._tokens[key]
        time_passed = now - last_time

        tokens += time_passed * rate
        if tokens > self.burst_limit:
            tokens = float(self.burst_limit)

        if tokens >= weight:
            self._tokens[key] = (now, tokens - weight)
            return True

        self._tokens[key] = (now, tokens)
        return False
