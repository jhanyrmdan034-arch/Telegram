"""Real-time delivery: WebSocket connection registry + optional Redis fan-out.

The registry is per-process. When more than one API instance is running, events
are published to Redis so that a socket connected to instance B still receives a
message produced by instance A. With a single instance Redis is optional.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import uuid
from collections import defaultdict
from typing import Any

from fastapi import WebSocket

log = logging.getLogger(__name__)

EVENT_CHANNEL = "messenger:events"


class ConnectionManager:
    def __init__(self) -> None:
        self._connections: dict[uuid.UUID, set[WebSocket]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def connect(self, user_id: uuid.UUID, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._connections[user_id].add(websocket)

    async def disconnect(self, user_id: uuid.UUID, websocket: WebSocket) -> None:
        async with self._lock:
            sockets = self._connections.get(user_id)
            if sockets:
                sockets.discard(websocket)
                if not sockets:
                    self._connections.pop(user_id, None)

    def online_user_ids(self) -> set[uuid.UUID]:
        return set(self._connections.keys())

    @property
    def connection_count(self) -> int:
        return sum(len(s) for s in self._connections.values())

    async def send_to_users(self, user_ids: list[uuid.UUID], payload: dict[str, Any]) -> None:
        message = json.dumps(payload, default=str)
        dead: list[tuple[uuid.UUID, WebSocket]] = []
        for user_id in user_ids:
            for websocket in list(self._connections.get(user_id, ())):
                try:
                    await websocket.send_text(message)
                except Exception:  # socket closed / broken pipe
                    dead.append((user_id, websocket))
        for user_id, websocket in dead:
            await self.disconnect(user_id, websocket)


class InMemoryEventBus:
    """Default bus: single-process deployment, no extra moving parts."""

    name = "inmemory"

    def __init__(self, manager: ConnectionManager) -> None:
        self.manager = manager

    async def publish(self, user_ids: list[uuid.UUID], event: dict[str, Any]) -> None:
        await self.manager.send_to_users(user_ids, event)

    async def start(self) -> None:  # pragma: no cover - symmetry with RedisEventBus
        return None

    async def stop(self) -> None:  # pragma: no cover
        return None


class RedisEventBus:
    """Publishes to Redis; every instance (including this one) delivers locally.

    This guarantees that events reach sockets regardless of which worker handled
    the originating HTTP request.
    """

    name = "redis"

    def __init__(self, manager: ConnectionManager, redis_url: str) -> None:
        import redis.asyncio as aioredis  # local import keeps redis optional

        self.manager = manager
        self._redis = aioredis.from_url(redis_url, decode_responses=True)
        self._task: asyncio.Task | None = None

    async def publish(self, user_ids: list[uuid.UUID], event: dict[str, Any]) -> None:
        if not user_ids:
            return
        payload = json.dumps({"user_ids": [str(u) for u in user_ids], "event": event}, default=str)
        await self._redis.publish(EVENT_CHANNEL, payload)
        # Publish is also delivered to local sockets through the subscription loop.

    async def start(self) -> None:
        self._task = asyncio.create_task(self._subscribe())

    async def _subscribe(self) -> None:
        pubsub = self._redis.pubsub()
        await pubsub.subscribe(EVENT_CHANNEL)
        try:
            async for message in pubsub.listen():
                if message.get("type") != "message":
                    continue
                try:
                    data = json.loads(message["data"])
                    ids = [uuid.UUID(uid) for uid in data["user_ids"]]
                    await self.manager.send_to_users(ids, data["event"])
                except Exception:  # malformed payload must not kill the loop
                    log.exception("failed to dispatch realtime event")
        finally:
            with contextlib.suppress(Exception):
                await pubsub.unsubscribe(EVENT_CHANNEL)
                await pubsub.aclose()

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        await self._redis.aclose()
