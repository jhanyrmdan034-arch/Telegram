"""WebSocket endpoint: real-time events, typing indicators and read receipts."""
from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime

import jwt
from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from ..models import ChatMember, Session, User
from ..security import decode_access_token
from ..services import member_ids, require_membership

log = logging.getLogger(__name__)
router = APIRouter()


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, token: str = Query(...)):
    runtime = websocket.app.state.runtime
    try:
        payload = decode_access_token(runtime.settings, token)
        user_id = uuid.UUID(payload["sub"])
        session_id = uuid.UUID(payload["sid"])
    except (jwt.PyJWTError, KeyError, ValueError):
        await websocket.close(code=4401)
        return

    async with runtime.session_factory() as db:
        session = await db.get(Session, session_id)
        if session is None or session.revoked_at is not None or session.user_id != user_id:
            await websocket.close(code=4401)
            return
        user = await db.get(User, user_id)
        if user is None or user.is_blocked:
            await websocket.close(code=4403)
            return

    await runtime.manager.connect(user_id, websocket)
    await websocket.send_text(json.dumps({"type": "ready", "data": {"user_id": str(user_id)}}))

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_text(json.dumps({"type": "error", "data": {"detail": "invalid json"}}))
                continue

            event_type = message.get("type")
            data = message.get("data") or {}

            if event_type == "ping":
                await websocket.send_text(json.dumps({"type": "pong", "data": {}}))
                continue

            if event_type in {"typing", "read"}:
                chat_id_raw = data.get("chat_id")
                try:
                    chat_id = uuid.UUID(str(chat_id_raw))
                except (ValueError, TypeError):
                    await websocket.send_text(
                        json.dumps({"type": "error", "data": {"detail": "invalid chat_id"}})
                    )
                    continue
                async with runtime.session_factory() as db:
                    try:
                        _chat, member = await require_membership(db, chat_id, user_id)
                    except Exception:
                        await websocket.send_text(
                            json.dumps({"type": "error", "data": {"detail": "not a member"}})
                        )
                        continue
                    recipients = [uid for uid in await member_ids(db, chat_id) if uid != user_id]
                    if event_type == "typing":
                        await runtime.bus.publish(recipients, {
                            "type": "typing", "data": {"chat_id": str(chat_id), "user_id": str(user_id)},
                        })
                    else:
                        seq = int(data.get("seq") or 0)
                        if seq > member.last_read_seq:
                            member.last_read_seq = seq
                            await db.commit()
                        await runtime.bus.publish(recipients, {
                            "type": "message.read",
                            "data": {"chat_id": str(chat_id), "user_id": str(user_id), "seq": seq},
                        })
                continue

            await websocket.send_text(
                json.dumps({"type": "error", "data": {"detail": f"unknown event {event_type!r}"}})
            )
    except WebSocketDisconnect:
        pass
    except Exception:  # pragma: no cover - transport level failure
        log.exception("websocket failure for user %s", user_id)
    finally:
        await runtime.manager.disconnect(user_id, websocket)
