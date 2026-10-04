"""Rate limiting (CONTRACTS §11/§10): token bucket, Redis-backed when reachable, in-memory otherwise.

Nothing user-facing may 500 because Redis is down — connectivity failures fall back
to the process-local bucket and log a warning (§10).
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse, Response

from app.core.config import Settings
from app.core.errors import RateLimited, error_body

logger = logging.getLogger("capacityexchange.ratelimit")


@dataclass
class _Bucket:
    tokens: float
    updated_at: float


class TokenBucket:
    """In-memory per-key token bucket. Capacity = rate_limit_per_min, refill = same per 60s."""

    def __init__(self) -> None:
        self._buckets: dict[str, _Bucket] = {}
        self._lock = threading.Lock()

    def consume(self, key: str, capacity: float, per_seconds: float = 60.0,
                now: float | None = None) -> tuple[bool, float]:
        """Return (allowed, retry_after_seconds)."""
        now = now if now is not None else time.monotonic()
        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                self._buckets[key] = _Bucket(tokens=capacity - 1.0, updated_at=now)
                return True, 0.0
            refill_rate = capacity / per_seconds
            bucket.tokens = min(capacity, bucket.tokens + (now - bucket.updated_at) * refill_rate)
            bucket.updated_at = now
            if bucket.tokens >= 1.0:
                bucket.tokens -= 1.0
                return True, 0.0
            missing = 1.0 - bucket.tokens
            return False, missing / refill_rate

    def sweep(self, older_than: float = 300.0) -> None:
        now = time.monotonic()
        with self._lock:
            stale = [k for k, b in self._buckets.items() if now - b.updated_at > older_than]
            for k in stale:
                del self._buckets[k]


class RateLimiter:
    """Facade choosing Redis fixed-window counters when REDIS_URL is reachable, else TokenBucket."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.capacity = float(max(1, settings.rate_limit_per_min))
        self.memory = TokenBucket()
        self._redis = None
        if settings.redis_enabled:
            try:
                import redis  # sync client for a tiny ping; async used per-request via asyncio
                client = redis.Redis.from_url(settings.redis_url, socket_connect_timeout=1.0,
                                              socket_timeout=1.0)
                client.ping()
                self._redis = client
                logger.info("ratelimit: using Redis at %s", settings.redis_url)
            except Exception as exc:  # noqa: BLE001 — any Redis problem => in-memory (§10)
                logger.warning("ratelimit: Redis unreachable (%s); falling back to in-memory buckets", exc)
                self._redis = None

    def allow(self, key: str) -> tuple[bool, float]:
        if self._redis is not None:
            try:
                window = 60
                rk = f"rl:{key}"
                count = self._redis.incr(rk)
                if count == 1:
                    self._redis.expire(rk, window)
                if count <= self.capacity:
                    return True, 0.0
                ttl = max(self._redis.ttl(rk), 1)
                return False, float(ttl)
            except Exception as exc:  # noqa: BLE001
                logger.warning("ratelimit: Redis error (%s); using in-memory for this request", exc)
        return self.memory.consume(key, self.capacity)

    async def close(self) -> None:
        if self._redis is not None:
            try:
                self._redis.close()
            except Exception:  # noqa: BLE001
                pass


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Applies to /api/v1 only; keyed by client IP. Emits the §2 429 envelope."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path
        if not path.startswith("/api/v1"):
            return await call_next(request)
        limiter: RateLimiter | None = getattr(request.app.state, "ratelimiter", None)
        if limiter is None:
            return await call_next(request)
        key = (request.client.host if request.client else "unknown")
        allowed, retry_after = limiter.allow(key)
        if not allowed:
            exc = RateLimited(retry_after_seconds=max(1, int(retry_after + 0.999)))
            resp = JSONResponse(
                status_code=exc.http_status,
                content=error_body(exc.code, exc.message, exc.details,
                                   getattr(request.state, "request_id", "unknown")),
                headers={"Retry-After": str(exc.details["retry_after_seconds"])},
            )
            return resp
        return await call_next(request)
