"""Domain helpers shared by routers: membership, sequencing, auditing, events."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Iterable

from fastapi import HTTPException, Request, status
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from .models import (
    AuditLog,
    Chat,
    ChatMember,
    Contact,
    Message,
    User,
)
from .runtime import Runtime

# --------------------------------------------------------------------------- #
# Events
# --------------------------------------------------------------------------- #
async def emit(runtime: Runtime, user_ids: Iterable[uuid.UUID], event_type: str,
               data: dict[str, Any]) -> None:
    ids = list({uid for uid in user_ids})
    if not ids:
        return
    await runtime.bus.publish(ids, {"type": event_type, "data": data})


# --------------------------------------------------------------------------- #
# Auditing
# --------------------------------------------------------------------------- #
async def audit(db: AsyncSession, request: Request | None, actor_id: uuid.UUID | None,
                action: str, target_type: str | None = None, target_id: str | None = None,
                meta: dict[str, Any] | None = None) -> None:
    ip = None
    if request is not None:
        forwarded = request.headers.get("x-forwarded-for")
        ip = forwarded.split(",")[0].strip() if forwarded else (
            request.client.host if request.client else None
        )
    db.add(
        AuditLog(
            actor_id=actor_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            ip=ip,
            meta=meta or {},
        )
    )
    await db.commit()


# --------------------------------------------------------------------------- #
# Chats
# --------------------------------------------------------------------------- #
def private_key_for(a: uuid.UUID, b: uuid.UUID) -> str:
    return ":".join(sorted([str(a), str(b)]))


async def next_chat_seq(db: AsyncSession, chat_id: uuid.UUID) -> int:
    """Atomically allocates the next per-chat sequence number.

    `UPDATE ... RETURNING` keeps ordering correct under concurrency without an
    application-level lock.
    """
    result = await db.execute(
        text("UPDATE chats SET last_seq = last_seq + 1 WHERE id = :chat_id RETURNING last_seq"),
        {"chat_id": str(chat_id)},
    )
    row = result.first()
    if row is None:
        raise HTTPException(status_code=404, detail="Chat not found")
    return int(row[0])


async def get_membership(db: AsyncSession, chat_id: uuid.UUID,
                         user_id: uuid.UUID) -> ChatMember | None:
    member = await db.get(ChatMember, {"chat_id": chat_id, "user_id": user_id})
    if member is None or member.left_at is not None:
        return None
    return member


async def require_membership(db: AsyncSession, chat_id: uuid.UUID, user_id: uuid.UUID,
                             minimum_role: str | None = None) -> tuple[Chat, ChatMember]:
    chat = await db.get(Chat, chat_id)
    if chat is None:
        raise HTTPException(status_code=404, detail="Chat not found")
    member = await get_membership(db, chat_id, user_id)
    if member is None:
        raise HTTPException(status_code=403, detail="You are not a member of this chat")
    if minimum_role == "admin" and member.role not in {"owner", "admin"}:
        raise HTTPException(status_code=403, detail="Administrator rights required")
    if minimum_role == "owner" and member.role != "owner":
        raise HTTPException(status_code=403, detail="Owner rights required")
    return chat, member


async def member_ids(db: AsyncSession, chat_id: uuid.UUID) -> list[uuid.UUID]:
    result = await db.execute(
        select(ChatMember.user_id).where(ChatMember.chat_id == chat_id, ChatMember.left_at.is_(None))
    )
    return [row[0] for row in result.all()]


async def member_ids_bulk(db: AsyncSession, chat_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[uuid.UUID]]:
    if not chat_ids:
        return {}
    result = await db.execute(
        select(ChatMember.chat_id, ChatMember.user_id).where(
            ChatMember.chat_id.in_(chat_ids), ChatMember.left_at.is_(None)
        )
    )
    grouped: dict[uuid.UUID, list[uuid.UUID]] = {cid: [] for cid in chat_ids}
    for chat_id, user_id in result.all():
        grouped.setdefault(chat_id, []).append(user_id)
    return grouped


# --------------------------------------------------------------------------- #
# Messages
# --------------------------------------------------------------------------- #
def message_payload(message: Message) -> dict[str, Any]:
    return {
        "id": str(message.id),
        "chat_id": str(message.chat_id),
        "sender_id": str(message.sender_id) if message.sender_id else None,
        "seq": message.seq,
        "type": message.type,
        "text": message.text,
        "entities": message.entities,
        "reply_to_id": str(message.reply_to_id) if message.reply_to_id else None,
        "file_id": str(message.file_id) if message.file_id else None,
        "meta": message.meta or {},
        "created_at": message.created_at.isoformat() if message.created_at else None,
        "edited_at": message.edited_at.isoformat() if message.edited_at else None,
    }


def chat_payload(
    chat: Chat,
    member: ChatMember | None = None,
    *,
    last_message: dict[str, Any] | None = None,
    peer: dict[str, Any] | None = None,
) -> dict[str, Any]:
    unread = 0
    if member is not None:
        unread = max(0, chat.last_seq - member.last_read_seq)
    return {
        "id": str(chat.id),
        "type": chat.type,
        "title": chat.title,
        "username": chat.username,
        "about": chat.about,
        "avatar_file_id": str(chat.avatar_file_id) if chat.avatar_file_id else None,
        "owner_id": str(chat.owner_id) if chat.owner_id else None,
        "is_public": chat.is_public,
        "member_count": chat.member_count,
        "last_seq": chat.last_seq,
        "created_at": chat.created_at.isoformat() if chat.created_at else None,
        "my_role": member.role if member else None,
        "last_read_seq": member.last_read_seq if member else 0,
        "unread_count": unread,
        "pinned": bool(member.is_pinned) if member else False,
        "archived": bool(member.archived) if member else False,
        "last_message": last_message,
        "peer": peer,
    }


async def bulk_last_messages(db: AsyncSession, chat_ids: list[uuid.UUID]) -> dict[uuid.UUID, dict]:
    """One query for the whole dialog list (DISTINCT ON keeps it index-friendly)."""
    if not chat_ids:
        return {}
    rows = (await db.execute(
        text("""
            SELECT DISTINCT ON (chat_id)
                   chat_id, id, sender_id, seq, type, text, created_at
            FROM messages
            WHERE deleted_at IS NULL AND chat_id = ANY(:ids)
            ORDER BY chat_id, seq DESC
        """),
        {"ids": chat_ids},
    )).all()
    return {
        row[0]: {
            "id": str(row[1]),
            "sender_id": str(row[2]) if row[2] else None,
            "seq": int(row[3]),
            "type": row[4],
            "text": row[5],
            "created_at": row[6].isoformat() if row[6] else None,
        }
        for row in rows
    }


async def presence_recipients(db: AsyncSession, user_id: uuid.UUID) -> list[uuid.UUID]:
    """Everyone who shares a chat with the user, so they can see online/offline."""
    rows = (await db.execute(
        select(ChatMember.user_id)
        .where(
            ChatMember.chat_id.in_(
                select(ChatMember.chat_id).where(ChatMember.user_id == user_id)
            ),
            ChatMember.user_id != user_id,
            ChatMember.left_at.is_(None),
        )
        .distinct()
    )).scalars().all()
    return list(rows)


async def is_contact(db: AsyncSession, owner_id: uuid.UUID, target_id: uuid.UUID) -> bool:
    row = (await db.execute(
        select(Contact.id).where(Contact.owner_id == owner_id, Contact.contact_id == target_id)
    )).first()
    return row is not None


def presence_allowed(privacy: str, viewer_is_contact: bool) -> bool:
    if privacy == "nobody":
        return False
    if privacy == "contacts":
        return viewer_is_contact
    return True


async def user_public(db: AsyncSession, target, viewer_id: uuid.UUID,
                      online_ids: set[uuid.UUID] | None = None) -> dict:
    """One user as the viewer may see them: presence follows the privacy setting."""
    contact = await is_contact(db, target.id, viewer_id) or await is_contact(db, viewer_id, target.id)
    allowed = presence_allowed(target.last_seen_privacy, contact)
    online = bool(online_ids and target.id in online_ids)
    return {
        "id": target.id,
        "username": target.username,
        "display_name": target.display_name,
        "bio": target.bio,
        "avatar_file_id": target.avatar_file_id,
        "last_seen_at": target.last_seen_at if allowed else None,
        "is_online": online if allowed else False,
        "presence_visible": allowed,
    }


async def bulk_private_peers(db: AsyncSession, chat_ids: list[uuid.UUID],
                             viewer_id: uuid.UUID) -> dict[uuid.UUID, dict]:
    """Peer summary for private/saved chats so the client can render avatar + title."""
    if not chat_ids:
        return {}
    rows = (await db.execute(
        select(ChatMember.chat_id, User)
        .join(User, User.id == ChatMember.user_id)
        .where(
            ChatMember.chat_id.in_(chat_ids),
            ChatMember.user_id != viewer_id,
            ChatMember.left_at.is_(None),
        )
    )).all()
    return {
        chat_id: {
            "id": str(user.id),
            "username": user.username,
            "display_name": user.display_name,
            "avatar_file_id": str(user.avatar_file_id) if user.avatar_file_id else None,
            # chats imply a relationship, so presence follows the same policy but
            # never leaks to someone the settings hide it from.
            "last_seen_at": (user.last_seen_at.isoformat()
                             if user.last_seen_at and user.last_seen_privacy != "nobody" else None),
            "is_online": user.last_seen_privacy == "everybody",
            "presence_visible": user.last_seen_privacy != "nobody",
        }
        for chat_id, user in rows
    }


# --------------------------------------------------------------------------- #
# Accounts
# --------------------------------------------------------------------------- #
def account_usable(user: User) -> bool:
    now = datetime.now(UTC)
    if user.is_blocked or not user.is_active:
        return False
    if user.suspended_until is not None:
        until = user.suspended_until if user.suspended_until.tzinfo else user.suspended_until.replace(
            tzinfo=UTC
        )
        if until > now:
            return False
    return True


def ensure_not_self(target: uuid.UUID, actor: uuid.UUID) -> None:
    if target == actor:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot target yourself")


async def attach_file_payloads(db, payloads: list[dict]) -> None:
    """Adds file metadata (name/size/mime/dimensions) next to each file_id."""
    from sqlalchemy import select

    from .models import File

    ids = {payload["file_id"] for payload in payloads if payload.get("file_id")}
    if not ids:
        return
    rows = {str(row.id): row for row in
            (await db.execute(select(File).where(File.id.in_(ids)))).scalars().all()}
    for payload in payloads:
        record = rows.get(payload.get("file_id") or "")
        if record is None:
            continue
        payload["file"] = {
            "id": str(record.id), "name": record.name, "mime_type": record.mime_type,
            "size": record.size, "kind": record.kind,
            "width": record.width, "height": record.height,
        }
