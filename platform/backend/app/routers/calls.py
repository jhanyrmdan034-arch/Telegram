"""Voice/video call signalling.

This service owns call state and signalling fan-out. Media itself is relayed by
an external TURN/SFU (see deploy/coturn). Clients receive ICE credentials from
/calls/ice and exchange SDP through the WebSocket `call.signal` event.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from ..deps import CurrentUser, DbDep, RuntimeDep
from ..models import Call, Chat, ChatMember
from ..schemas import CallCreate, CallOut, CallUpdate
from ..services import emit, member_ids, require_membership

router = APIRouter(tags=["calls"])


@router.get("/calls/ice")
async def ice_servers(user: CurrentUser, runtime: RuntimeDep):
    turn_url = runtime.settings.__dict__.get("turn_url")
    if not turn_url:
        return {"ice_servers": [{"urls": "stun:stun.l.google.com:19302"}]}
    return {
        "ice_servers": [
            {"urls": "stun:stun.l.google.com:19302"},
            {
                "urls": turn_url,
                "username": runtime.settings.__dict__.get("turn_username"),
                "credential": runtime.settings.__dict__.get("turn_password"),
            },
        ]
    }


@router.post("/chats/{chat_id}/calls", response_model=CallOut, status_code=201)
async def start_call(chat_id: uuid.UUID, payload: CallCreate, user: CurrentUser, db: DbDep,
                     runtime: RuntimeDep):
    chat, _member = await require_membership(db, chat_id, user.id)
    participants = payload.participant_ids or [uid for uid in await member_ids(db, chat_id)
                                               if uid != user.id]
    if not participants:
        raise HTTPException(status_code=400, detail="No call participants")

    call = Call(
        chat_id=chat_id,
        initiator_id=user.id,
        kind=payload.kind,
        status="ringing",
        participant_ids=[str(uid) for uid in participants],
    )
    db.add(call)
    await db.commit()
    await db.refresh(call)

    await emit(runtime, participants, "call.incoming", {
        "call_id": str(call.id),
        "chat_id": str(chat_id),
        "initiator_id": str(user.id),
        "kind": call.kind,
    })
    return call


@router.post("/calls/{call_id}/status", response_model=CallOut)
async def update_call(call_id: uuid.UUID, payload: CallUpdate, user: CurrentUser, db: DbDep,
                      runtime: RuntimeDep):
    call = await db.get(Call, call_id)
    if call is None:
        raise HTTPException(status_code=404, detail="Call not found")
    allowed = {call.initiator_id} | {uuid.UUID(uid) for uid in call.participant_ids}
    if user.id not in allowed:
        raise HTTPException(status_code=403, detail="Not a participant of this call")

    now = datetime.now(UTC)
    call.status = payload.status
    if payload.status == "active" and call.started_at is None:
        call.started_at = now
    if payload.status in {"ended", "declined", "missed", "failed"}:
        call.ended_at = now
        call.end_reason = payload.end_reason
    await db.commit()
    await db.refresh(call)

    recipients = list(allowed - {user.id})
    await emit(runtime, recipients, "call.updated", {
        "call_id": str(call.id), "status": call.status,
    })
    return call


@router.get("/calls/history", response_model=list[CallOut])
async def call_history(user: CurrentUser, db: DbDep, limit: int = Query(default=50, ge=1, le=200)):
    member_chats = select(ChatMember.chat_id).where(
        ChatMember.user_id == user.id, ChatMember.left_at.is_(None)
    )
    result = await db.execute(
        select(Call)
        .where(Call.chat_id.in_(member_chats))
        .order_by(Call.created_at.desc())
        .limit(limit)
    )
    return list(result.scalars())
