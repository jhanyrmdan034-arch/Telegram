"""Message creation, history pagination, editing and deletion."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select

from ..deps import CurrentUser, DbDep, RuntimeDep
from ..models import ChatMember, File, Message
from ..schemas import ForwardRequest, MessageCreate, MessageOut, MessagePage, MessageUpdate
from ..services import (
    emit,
    member_ids,
    message_payload,
    next_chat_seq,
    require_membership,
)

router = APIRouter(tags=["messages"])

MEDIA_TYPES = {"photo", "video", "document", "voice", "sticker"}


@router.post("/chats/{chat_id}/messages", response_model=MessageOut, status_code=201)
async def send_message(chat_id: uuid.UUID, payload: MessageCreate, user: CurrentUser, db: DbDep,
                       runtime: RuntimeDep):
    chat, member = await require_membership(db, chat_id, user.id)

    if chat.type == "channel" and member.role not in {"owner", "admin"}:
        raise HTTPException(status_code=403, detail="Only channel administrators can post")

    if payload.type == "text" and not (payload.text and payload.text.strip()):
        raise HTTPException(status_code=422, detail="Text messages require non-empty text")
    if payload.type in MEDIA_TYPES and payload.file_id is None:
        raise HTTPException(status_code=422, detail=f"{payload.type} messages require a file_id")
    if payload.file_id is not None:
        file = await db.get(File, payload.file_id)
        if file is None:
            raise HTTPException(status_code=404, detail="File not found")
        if file.owner_id != user.id:
            raise HTTPException(status_code=403, detail="File does not belong to you")
    if payload.reply_to_id is not None:
        replied = await db.get(Message, payload.reply_to_id)
        if replied is None or replied.chat_id != chat_id:
            raise HTTPException(status_code=404, detail="Replied message not found in this chat")

    if payload.client_message_id:
        # A retry after a dropped connection re-sends the same client id; return the
        # message that already exists instead of duplicating the bubble.
        existing = (await db.execute(
            select(Message).where(
                Message.chat_id == chat_id,
                Message.deleted_at.is_(None),
                Message.meta["client_message_id"].astext == payload.client_message_id,
            )
        )).scalars().first()
        if existing is not None:
            return existing

    seq = await next_chat_seq(db, chat_id)
    message = Message(
        chat_id=chat_id,
        sender_id=user.id,
        seq=seq,
        type=payload.type,
        text=payload.text,
        entities=payload.entities,
        reply_to_id=payload.reply_to_id,
        file_id=payload.file_id,
        meta={**payload.meta, **({"client_message_id": payload.client_message_id}
                                 if payload.client_message_id else {})},
    )
    db.add(message)
    chat.updated_at = datetime.now(UTC)
    member.last_read_seq = seq
    await db.commit()
    await db.refresh(message)

    recipients = await member_ids(db, chat_id)
    await emit(runtime, recipients, "message.new", message_payload(message))
    if message.file_id is not None:
        # Media gets its own event so a client can fetch the file before the bubble
        # needs it, without duplicating the message (same message id, same frame id).
        await emit(runtime, recipients, "message.media", message_payload(message))
        await emit(runtime, recipients, "media.ready", {
            "file_id": str(message.file_id), "message_id": str(message.id),
            "chat_id": str(chat_id),
        })
    return message


@router.get("/chats/{chat_id}/messages", response_model=MessagePage)
async def get_messages(
    chat_id: uuid.UUID,
    user: CurrentUser,
    db: DbDep,
    before_seq: int | None = Query(default=None, ge=1),
    after_seq: int | None = Query(default=None, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
):
    await require_membership(db, chat_id, user.id)
    stmt = (
        select(Message)
        .where(Message.chat_id == chat_id, Message.deleted_at.is_(None))
        .order_by(Message.seq.desc())
        .limit(limit + 1)
    )
    if before_seq is not None:
        stmt = stmt.where(Message.seq < before_seq)
    if after_seq is not None:
        # Forward paging: used to fill the gap after the socket reconnects.
        stmt = (
            select(Message)
            .where(Message.chat_id == chat_id, Message.deleted_at.is_(None),
                   Message.seq > after_seq)
            .order_by(Message.seq.asc())
            .limit(limit)
        )
    result = await db.execute(stmt)
    rows = list(result.scalars())
    has_more = len(rows) > limit
    rows = rows[:limit]
    ordered = rows if after_seq is not None else list(reversed(rows))
    items = [MessageOut.model_validate(m) for m in ordered]
    next_before = rows[-1].seq if has_more and rows and after_seq is None else None
    return MessagePage(items=items, next_before_seq=next_before)


@router.patch("/messages/{message_id}", response_model=MessageOut)
async def edit_message(message_id: uuid.UUID, payload: MessageUpdate, user: CurrentUser, db: DbDep,
                       runtime: RuntimeDep):
    message = await db.get(Message, message_id)
    if message is None or message.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Message not found")
    if message.sender_id != user.id:
        raise HTTPException(status_code=403, detail="Only the sender can edit this message")
    if message.type != "text":
        raise HTTPException(status_code=400, detail="Only text messages can be edited")
    message.text = payload.text
    message.edited_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(message)
    await emit(runtime, await member_ids(db, message.chat_id), "message.edited",
               message_payload(message))
    return message


@router.delete("/messages/{message_id}", status_code=204)
async def delete_message(message_id: uuid.UUID, user: CurrentUser, db: DbDep, runtime: RuntimeDep):
    message = await db.get(Message, message_id)
    if message is None or message.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Message not found")

    allowed = message.sender_id == user.id
    if not allowed:
        member = await db.get(ChatMember, {"chat_id": message.chat_id, "user_id": user.id})
        allowed = member is not None and member.role in {"owner", "admin"} and member.left_at is None
    if not allowed:
        raise HTTPException(status_code=403, detail="Not allowed to delete this message")

    message.deleted_at = datetime.now(UTC)
    message.text = None
    await db.commit()
    await emit(runtime, await member_ids(db, message.chat_id), "message.deleted",
               {"id": str(message.id), "chat_id": str(message.chat_id)})
    return None


@router.post("/messages/{message_id}/forward", response_model=list[MessageOut], status_code=201)
async def forward_message(message_id: uuid.UUID, payload: ForwardRequest, user: CurrentUser,
                          db: DbDep, runtime: RuntimeDep):
    source = await db.get(Message, message_id)
    if source is None or source.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Message not found")
    await require_membership(db, source.chat_id, user.id)

    targets: list[tuple[uuid.UUID, ChatMember]] = []
    for chat_id in dict.fromkeys(payload.chat_ids):
        chat, member = await require_membership(db, chat_id, user.id)
        if chat.type == "channel" and member.role not in {"owner", "admin"}:
            raise HTTPException(status_code=403, detail="Only channel administrators can post")
        targets.append((chat_id, member))

    created: list[Message] = []
    forward_meta = {
        "forwarded_from": {
            "message_id": str(source.id),
            "chat_id": str(source.chat_id),
            "sender_id": str(source.sender_id) if source.sender_id else None,
        }
    }
    for chat_id, member in targets:
        seq = await next_chat_seq(db, chat_id)
        copy = Message(
            chat_id=chat_id,
            sender_id=user.id,
            seq=seq,
            type=source.type,
            text=source.text,
            entities=source.entities,
            file_id=source.file_id,
            meta=forward_meta,
        )
        db.add(copy)
        member.last_read_seq = seq
        created.append(copy)

    await db.commit()
    for message in created:
        await db.refresh(message)
        await emit(runtime, await member_ids(db, message.chat_id), "message.new",
                   message_payload(message))
    return created


@router.get("/chats/{chat_id}/messages/search", response_model=list[MessageOut])
async def search_messages(chat_id: uuid.UUID, user: CurrentUser, db: DbDep,
                          q: str = Query(min_length=1, max_length=128),
                          limit: int = Query(default=50, ge=1, le=200)):
    await require_membership(db, chat_id, user.id)
    result = await db.execute(
        select(Message)
        .where(
            Message.chat_id == chat_id,
            Message.deleted_at.is_(None),
            Message.text.ilike(f"%{q.strip()}%"),
        )
        .order_by(Message.seq.desc())
        .limit(limit)
    )
    return list(result.scalars())


@router.get("/chats/{chat_id}/messages/{message_id}", response_model=MessageOut, status_code=200)
async def get_message(chat_id: uuid.UUID, message_id: uuid.UUID, user: CurrentUser, db: DbDep):
    await require_membership(db, chat_id, user.id)
    message = await db.get(Message, message_id)
    if message is None or message.chat_id != chat_id or message.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Message not found")
    return message
