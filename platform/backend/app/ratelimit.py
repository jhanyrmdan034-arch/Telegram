"""Fixed-window rate limiting backed by Redis, with an in-process fallback.

Redis is authoritative when configured; the memory store keeps single-node
deployments and tests working without extra infrastructure.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Protocol

from fastapi import HTTPException, Request, status


class CounterStore(Protocol):
    async def incr(self, key: str, window: int) -> int: ...


@dataclass
class RateLimitResult:
    allowed: bool
    remaining: int
    retry_after: int


class MemoryCounterStore:
    def __init__(self) -> None:
        self._buckets: dict[str, tuple[int, int]] = {}  # key -> (window_start, count)

    async def incr(self, key: str, window: int) -> int:
        now = int(time.time())
        start, count = self._buckets.get(key, (now, 0))
        if now - start >= window:
            start, count = now, 0
        count += 1
        self._buckets[key] = (start, count)
        if len(self._buckets) > 100_000:  # crude bound to avoid unbounded growth
            self._buckets = {k: v for k, v in self._buckets.items() if now - v[0] < window}
        return count


class RedisCounterStore:
    def __init__(self, redis_url: str) -> None:
        import redis.asyncio as aioredis

        self._redis = aioredis.from_url(redis_url, decode_responses=True)

    async def incr(self, key: str, window: int) -> int:
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.incr(key)
            pipe.expire(key, window, nx=True)
            count, _ = await pipe.execute()
        return int(count)


class RateLimiter:
    def __init__(self, store: CounterStore, requests: int, window: int) -> None:
        self.store = store
        self.requests = requests
        self.window = window

    async def check(self, identity: str) -> RateLimitResult:
        count = await self.store.incr(f"ratelimit:{identity}", self.window)
        if count > self.requests:
            return RateLimitResult(False, 0, self.window)
        return RateLimitResult(True, self.requests - count, self.window)

    async def enforce(self, identity: str) -> None:
        result = await self.check(identity)
        if not result.allowed:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many requests",
                headers={"Retry-After": str(result.retry_after)},
            )


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"
