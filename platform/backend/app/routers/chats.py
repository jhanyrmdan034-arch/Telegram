"""Private chats, groups, channels and membership management."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select

from ..deps import CurrentUser, DbDep, RuntimeDep
from ..models import Chat, ChatMember, User
from ..schemas import (
    ChatArchive,
    ChatCreate,
    ChatPin,
    ChatMemberOut,
    ChatOut,
    ChatUpdate,
    MemberAdd,
    MemberUpdate,
)
from ..services import (
    bulk_last_messages,
    bulk_private_peers,
    chat_payload,
    emit,
    get_membership,
    member_ids,
    private_key_for,
    require_membership,
)

router = APIRouter(prefix="/chats", tags=["chats"])


async def _get_or_create_private(db, user_id: uuid.UUID, other_id: uuid.UUID) -> Chat:
    key = private_key_for(user_id, other_id)
    existing = await db.execute(select(Chat).where(Chat.private_key == key))
    chat = existing.scalar_one_or_none()
    if chat is not None:
        return chat

    chat = Chat(type="private", private_key=key, member_count=2, title=None)
    db.add(chat)
    await db.flush()
    db.add_all([
        ChatMember(chat_id=chat.id, user_id=user_id, role="member"),
        ChatMember(chat_id=chat.id, user_id=other_id, role="member"),
    ])
    await db.commit()
    await db.refresh(chat)
    return chat


async def enrich_chats(db, runtime, pairs: list[tuple[Chat, ChatMember | None]],
                       viewer_id: uuid.UUID) -> list[ChatOut]:
    """Attach last message, unread counter, peer summary and presence to chat rows."""
    chat_ids = [chat.id for chat, _ in pairs]
    last_messages = await bulk_last_messages(db, chat_ids)
    peers = await bulk_private_peers(db, chat_ids, viewer_id)
    online = {str(uid) for uid in runtime.manager.online_user_ids()}

    out: list[ChatOut] = []
    for chat, member in pairs:
        peer = peers.get(chat.id)
        if peer is not None:
            peer = {**peer, "is_online": peer["id"] in online}
        out.append(ChatOut(**chat_payload(
            chat, member,
            last_message=last_messages.get(chat.id),
            peer=peer,
        )))
    return out


@router.post("", response_model=ChatOut, status_code=status.HTTP_201_CREATED)
async def create_chat(payload: ChatCreate, user: CurrentUser, db: DbDep, runtime: RuntimeDep):
    if payload.type == "saved":
        key = f"saved:{user.id}"
        existing = await db.execute(select(Chat).where(Chat.private_key == key))
        chat = existing.scalar_one_or_none()
        if chat is not None:
            member = await get_membership(db, chat.id, user.id)
            return ChatOut(**chat_payload(chat, member))
        chat = Chat(type="saved", private_key=key, title="Saved Messages", member_count=1,
                    owner_id=user.id)
        db.add(chat)
        await db.flush()
        db.add(ChatMember(chat_id=chat.id, user_id=user.id, role="owner"))
        await db.commit()
        await db.refresh(chat)
        member = await get_membership(db, chat.id, user.id)
        return ChatOut(**chat_payload(chat, member))

    if payload.type == "private":
        if len(payload.member_ids) != 1:
            raise HTTPException(status_code=400, detail="A private chat needs exactly one peer")
        other_id = payload.member_ids[0]
        if other_id == user.id:
            raise HTTPException(status_code=400, detail="Cannot create a private chat with yourself")
        other = await db.get(User, other_id)
        if other is None or not other.is_active:
            raise HTTPException(status_code=404, detail="User not found")
        chat = await _get_or_create_private(db, user.id, other_id)
        member = await get_membership(db, chat.id, user.id)
        return ChatOut(**chat_payload(chat, member))

    # group / channel
    if not payload.title:
        raise HTTPException(status_code=400, detail="Title is required")
    members = [uid for uid in dict.fromkeys(payload.member_ids) if uid != user.id]
    if members:
        result = await db.execute(select(User.id).where(User.id.in_(members), User.is_active.is_(True)))
        found = {row[0] for row in result.all()}
        missing = set(members) - found
        if missing:
            raise HTTPException(status_code=404, detail=f"Unknown users: {sorted(map(str, missing))}")

    chat = Chat(
        type=payload.type,
        title=payload.title,
        about=payload.about,
        username=payload.username,
        is_public=payload.is_public,
        owner_id=user.id,
        member_count=len(members) + 1,
    )
    db.add(chat)
    await db.flush()
    db.add(ChatMember(chat_id=chat.id, user_id=user.id, role="owner"))
    for uid in members:
        db.add(ChatMember(chat_id=chat.id, user_id=uid, role="member"))
    await db.commit()
    await db.refresh(chat)

    await emit(runtime, [user.id, *members], "chat.created", chat_payload(chat))
    member = await get_membership(db, chat.id, user.id)
    return ChatOut(**chat_payload(chat, member))


@router.get("", response_model=list[ChatOut])
async def list_chats(
    user: CurrentUser,
    db: DbDep,
    runtime: RuntimeDep,
    type_: str | None = Query(default=None, alias="type"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    archived: bool = Query(default=False, description="Archived dialogs are paged separately"),
):
    stmt = (
        select(Chat, ChatMember)
        .join(ChatMember, ChatMember.chat_id == Chat.id)
        .where(ChatMember.user_id == user.id, ChatMember.left_at.is_(None),
               ChatMember.archived.is_(archived))
        .order_by(ChatMember.is_pinned.desc(), Chat.updated_at.desc())
        .limit(limit)
        .offset(offset)
    )
    if type_:
        stmt = stmt.where(Chat.type == type_)
    result = await db.execute(stmt)
    return await enrich_chats(db, runtime, list(result.all()), user.id)


@router.get("/pinned", response_model=list[ChatOut])
async def list_pinned(user: CurrentUser, db: DbDep, runtime: RuntimeDep):
    result = await db.execute(
        select(Chat, ChatMember)
        .join(ChatMember, ChatMember.chat_id == Chat.id)
        .where(ChatMember.user_id == user.id, ChatMember.left_at.is_(None),
               ChatMember.is_pinned.is_(True))
        .order_by(Chat.updated_at.desc())
    )
    return await enrich_chats(db, runtime, list(result.all()), user.id)


@router.get("/{chat_id}", response_model=ChatOut)
async def get_chat(chat_id: uuid.UUID, user: CurrentUser, db: DbDep, runtime: RuntimeDep):
    chat, member = await require_membership(db, chat_id, user.id)
    enriched = await enrich_chats(db, runtime, [(chat, member)], user.id)
    return enriched[0]


@router.patch("/{chat_id}", response_model=ChatOut)
async def update_chat(chat_id: uuid.UUID, payload: ChatUpdate, user: CurrentUser, db: DbDep):
    chat, member = await require_membership(db, chat_id, user.id, minimum_role="admin")
    if chat.type in {"private", "saved"}:
        raise HTTPException(status_code=400, detail="Private chats cannot be edited")
    if payload.title is not None:
        chat.title = payload.title
    if payload.about is not None:
        chat.about = payload.about
    if payload.username is not None:
        taken = await db.execute(
            select(Chat.id).where(Chat.username == payload.username, Chat.id != chat.id)
        )
        if taken.scalar_one_or_none() is not None:
            raise HTTPException(status_code=409, detail="Username is already taken")
        chat.username = payload.username
    if payload.is_public is not None:
        chat.is_public = payload.is_public
    if payload.avatar_file_id is not None:
        chat.avatar_file_id = payload.avatar_file_id
    chat.updated_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(chat)
    return ChatOut(**chat_payload(chat, member))


@router.delete("/{chat_id}", status_code=204)
async def delete_chat(chat_id: uuid.UUID, user: CurrentUser, db: DbDep):
    chat, _member = await require_membership(db, chat_id, user.id)
    if chat.type in {"private", "saved"}:
        raise HTTPException(status_code=400, detail="This chat type cannot be deleted")
    if _member.role != "owner":
        raise HTTPException(status_code=403, detail="Owner rights required")
    await db.delete(chat)
    await db.commit()
    return None


@router.get("/{chat_id}/members", response_model=list[ChatMemberOut])
async def list_members(
    chat_id: uuid.UUID,
    user: CurrentUser,
    db: DbDep,
    limit: int = Query(default=200, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
):
    await require_membership(db, chat_id, user.id)
    result = await db.execute(
        select(ChatMember, User)
        .join(User, User.id == ChatMember.user_id)
        .where(ChatMember.chat_id == chat_id, ChatMember.left_at.is_(None))
        .order_by(ChatMember.joined_at)
        .limit(limit)
        .offset(offset)
    )
    return [
        ChatMemberOut(
            user_id=member.user_id,
            username=other.username,
            display_name=other.display_name,
            role=member.role,
            joined_at=member.joined_at,
        )
        for member, other in result.all()
    ]


@router.post("/{chat_id}/members", response_model=list[ChatMemberOut], status_code=201)
async def add_members(chat_id: uuid.UUID, payload: MemberAdd, user: CurrentUser, db: DbDep,
                      runtime: RuntimeDep):
    chat, _member = await require_membership(db, chat_id, user.id, minimum_role="admin")
    if chat.type in {"private", "saved"}:
        raise HTTPException(status_code=400, detail="Cannot add members to this chat type")

    result = await db.execute(select(User.id).where(User.id.in_(payload.user_ids),
                                                   User.is_active.is_(True)))
    found = {row[0] for row in result.all()}
    missing = set(payload.user_ids) - found
    if missing:
        raise HTTPException(status_code=404, detail=f"Unknown users: {sorted(map(str, missing))}")

    added: list[ChatMember] = []
    for uid in dict.fromkeys(payload.user_ids):
        existing = await db.get(ChatMember, {"chat_id": chat_id, "user_id": uid})
        if existing is None:
            member = ChatMember(chat_id=chat_id, user_id=uid, role=payload.role)
            db.add(member)
            added.append(member)
        elif existing.left_at is not None:
            existing.left_at = None
            existing.role = payload.role
            added.append(existing)

    await db.flush()
    chat.member_count = int(
        (
            await db.execute(
                select(func.count()).select_from(ChatMember).where(
                    ChatMember.chat_id == chat_id, ChatMember.left_at.is_(None)
                )
            )
        ).scalar_one()
    )
    await db.commit()

    if added:
        await emit(runtime, await member_ids(db, chat_id), "chat.members_added",
                   {"chat_id": str(chat_id), "user_ids": [str(m.user_id) for m in added]})

    rows = await db.execute(
        select(ChatMember, User)
        .join(User, User.id == ChatMember.user_id)
        .where(ChatMember.chat_id == chat_id, ChatMember.user_id.in_([m.user_id for m in added]))
    ) if added else None
    if rows is None:
        return []
    return [
        ChatMemberOut(user_id=m.user_id, username=u.username, display_name=u.display_name,
                      role=m.role, joined_at=m.joined_at)
        for m, u in rows.all()
    ]


@router.patch("/{chat_id}/members/{member_id}", response_model=ChatMemberOut)
async def update_member(chat_id: uuid.UUID, member_id: uuid.UUID, payload: MemberUpdate,
                        user: CurrentUser, db: DbDep):
    _chat, actor = await require_membership(db, chat_id, user.id, minimum_role="admin")
    target = await db.get(ChatMember, {"chat_id": chat_id, "user_id": member_id})
    if target is None or target.left_at is not None:
        raise HTTPException(status_code=404, detail="Member not found")
    if target.role == "owner":
        raise HTTPException(status_code=403, detail="Owner role cannot be changed")
    if actor.role == "admin" and target.role == "admin":
        raise HTTPException(status_code=403, detail="Only the owner can manage administrators")
    target.role = payload.role
    await db.commit()
    other = await db.get(User, member_id)
    return ChatMemberOut(user_id=target.user_id, username=other.username if other else "",
                         display_name=other.display_name if other else "", role=target.role,
                         joined_at=target.joined_at)


@router.delete("/{chat_id}/members/{member_id}", status_code=204)
async def remove_member(chat_id: uuid.UUID, member_id: uuid.UUID, user: CurrentUser, db: DbDep,
                        runtime: RuntimeDep):
    chat, actor = await require_membership(db, chat_id, user.id)
    target = await db.get(ChatMember, {"chat_id": chat_id, "user_id": member_id})
    if target is None or target.left_at is not None:
        raise HTTPException(status_code=404, detail="Member not found")
    if member_id != user.id:
        if actor.role not in {"owner", "admin"}:
            raise HTTPException(status_code=403, detail="Administrator rights required")
        if target.role == "owner":
            raise HTTPException(status_code=403, detail="Owner cannot be removed")
        if actor.role == "admin" and target.role == "admin":
            raise HTTPException(status_code=403, detail="Only the owner can remove administrators")

    target.left_at = datetime.now(UTC)
    chat.member_count = max(0, chat.member_count - 1)
    await db.commit()
    await emit(runtime, [*await member_ids(db, chat_id), member_id], "chat.member_removed",
               {"chat_id": str(chat_id), "user_id": str(member_id)})
    return None


@router.post("/{chat_id}/leave", status_code=204)
async def leave_chat(chat_id: uuid.UUID, user: CurrentUser, db: DbDep, runtime: RuntimeDep):
    chat, member = await require_membership(db, chat_id, user.id)
    if member.role == "owner" and chat.type in {"group", "channel"}:
        raise HTTPException(
            status_code=409,
            detail="Transfer ownership before leaving the chat",
        )
    member.left_at = datetime.now(UTC)
    chat.member_count = max(0, chat.member_count - 1)
    await db.commit()
    await emit(runtime, await member_ids(db, chat_id), "chat.member_removed",
               {"chat_id": str(chat_id), "user_id": str(user.id)})
    return None


@router.post("/{chat_id}/pin", status_code=204)
async def pin_chat(chat_id: uuid.UUID, payload: ChatPin, user: CurrentUser,
                   db: DbDep, runtime: RuntimeDep):
    _chat, member = await require_membership(db, chat_id, user.id)
    member.is_pinned = payload.pinned
    await db.commit()
    await emit(runtime, await member_ids(db, chat_id), "dialog.updated",
               {"chat_id": str(chat_id), "user_id": str(user.id), "pinned": payload.pinned})
    return None


@router.post("/{chat_id}/archive", status_code=204)
async def archive_chat(chat_id: uuid.UUID, payload: ChatArchive, user: CurrentUser,
                       db: DbDep, runtime: RuntimeDep):
    _chat, member = await require_membership(db, chat_id, user.id)
    member.archived = payload.archived
    await db.commit()
    await emit(runtime, await member_ids(db, chat_id), "dialog.updated",
               {"chat_id": str(chat_id), "user_id": str(user.id), "archived": payload.archived})
    return None


@router.post("/{chat_id}/read", status_code=204)
async def mark_read(chat_id: uuid.UUID, user: CurrentUser, db: DbDep, runtime: RuntimeDep,
                    seq: int = Query(ge=0)):
    chat, member = await require_membership(db, chat_id, user.id)
    if seq > member.last_read_seq:
        member.last_read_seq = min(seq, chat.last_seq)
        await db.commit()
    recipients = await member_ids(db, chat_id)
    payload = {"chat_id": str(chat_id), "user_id": str(user.id), "seq": member.last_read_seq}
    # Two names for one fact: message.read is the transport event, read.updated is
    # what the dialog list and the chat screen subscribe to.
    await emit(runtime, recipients, "read.updated", payload)
    await emit(runtime, recipients, "message.read", payload)
    return None


@router.post("/{chat_id}/typing", status_code=204)
async def typing(chat_id: uuid.UUID, user: CurrentUser, db: DbDep, runtime: RuntimeDep):
    await require_membership(db, chat_id, user.id)
    recipients = [uid for uid in await member_ids(db, chat_id) if uid != user.id]
    await emit(runtime, recipients, "typing", {"chat_id": str(chat_id), "user_id": str(user.id)})
    return None
