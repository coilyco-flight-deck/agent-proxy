"""Admission rate limiting for the model-serving surface (issue #110).

A token bucket in front of the queue. The bounded queue already gives
backpressure once work is accepted, but backpressure is not a rate: a caller can
fill and drain the queue as fast as the backend serves, and nothing bounds the
arrival rate itself. This does, and it sheds rather than buffers, so a caller
learns immediately instead of waiting behind work it cannot see.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from .config import get_settings
from .obs import llm_rate_limited_total, log


def _now() -> float:
    return time.monotonic()


@dataclass
class TokenBucket:
    """Classic token bucket: ``rate`` tokens per second, capped at ``capacity``."""

    rate: float
    capacity: float
    tokens: float = 0.0
    updated_at: float = 0.0

    def take(self, now: float | None = None) -> bool:
        """Spend one token, or report that the caller is over its rate."""
        if self.rate <= 0:
            return True
        moment = _now() if now is None else now
        if self.updated_at == 0.0:
            self.tokens = self.capacity
            self.updated_at = moment
        elapsed = max(moment - self.updated_at, 0.0)
        self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
        self.updated_at = moment
        if self.tokens < 1.0:
            return False
        self.tokens -= 1.0
        return True

    def retry_after(self, now: float | None = None) -> float:
        """Seconds until one token is available, rounded up to a whole second."""
        if self.rate <= 0:
            return 0.0
        moment = _now() if now is None else now
        elapsed = max(moment - self.updated_at, 0.0)
        available = min(self.capacity, self.tokens + elapsed * self.rate)
        if available >= 1.0:
            return 0.0
        return max((1.0 - available) / self.rate, 0.0)


class RateLimiter:
    """One bucket per logical route, so a busy route cannot starve a quiet one."""

    def __init__(self) -> None:
        self._buckets: dict[str, TokenBucket] = {}

    def _bucket(self, key: str) -> TokenBucket:
        settings = get_settings()
        bucket = self._buckets.get(key)
        rate = settings.rate_limit_per_second
        capacity = float(max(settings.rate_limit_burst, 1))
        if bucket is None or bucket.rate != rate or bucket.capacity != capacity:
            bucket = TokenBucket(rate=rate, capacity=capacity)
            self._buckets[key] = bucket
        return bucket

    def allow(self, key: str) -> tuple[bool, float]:
        """Return ``(allowed, retry_after_seconds)`` for one request on ``key``."""
        bucket = self._bucket(key)
        if bucket.take():
            return True, 0.0
        retry_after = bucket.retry_after()
        llm_rate_limited_total.labels(logical_model=key).inc()
        log.warning("request.rate_limited", logical_model=key, retry_after=round(retry_after, 3))
        return False, retry_after

    def reset(self) -> None:
        """Drop every bucket. Test hook and controlled configuration reload."""
        self._buckets.clear()


_limiter = RateLimiter()


def get_rate_limiter() -> RateLimiter:
    return _limiter
