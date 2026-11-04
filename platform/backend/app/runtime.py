"""Per-process runtime container: infrastructure handles shared by all requests."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from .config import Settings
from .ratelimit import (
    MemoryCounterStore,
    RateLimiter,
    RedisCounterStore,
)
from .realtime import ConnectionManager, InMemoryEventBus, RedisEventBus
from .storage import LocalStorage


@dataclass
class Runtime:
    settings: Settings
    engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]
    storage: LocalStorage
    manager: ConnectionManager
    bus: Any
    limiter: RateLimiter
    login_limiter: RateLimiter
    redis: Any = None
    started_at: float = field(default_factory=time.time)
    version: str = "1.0.0"

    async def start(self) -> None:
        await self.bus.start()

    async def stop(self) -> None:
        await self.bus.stop()
        if self.redis is not None:
            await self.redis.aclose()
        await self.engine.dispose()


async def build_runtime(settings: Settings, engine: AsyncEngine,
                        session_factory: async_sessionmaker[AsyncSession]) -> Runtime:
    manager = ConnectionManager()
    redis_client = None
    use_redis = not settings.redis_url.startswith("memory://")

    if use_redis:
        import redis.asyncio as aioredis

        redis_client = aioredis.from_url(settings.redis_url, decode_responses=True)
        bus = RedisEventBus(manager, settings.redis_url)
        store = RedisCounterStore(settings.redis_url)
    else:
        bus = InMemoryEventBus(manager)
        store = MemoryCounterStore()

    limiter = RateLimiter(store, settings.rate_limit_requests, settings.rate_limit_window_seconds)
    login_limiter = RateLimiter(
        store, settings.login_rate_limit_requests, settings.login_rate_limit_window_seconds
    )
    return Runtime(
        settings=settings,
        engine=engine,
        session_factory=session_factory,
        storage=LocalStorage(settings.storage_dir),
        manager=manager,
        bus=bus,
        limiter=limiter,
        login_limiter=login_limiter,
        redis=redis_client,
        version="1.0.0",
    )
