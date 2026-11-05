"""User profiles, discovery, blocking and privacy."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import delete, func, or_, select

from ..deps import CurrentUser, DbDep, RuntimeDep
from ..models import (
    BlockedUser,
    ChatMember,
    Contact,
    User,
)
from ..services import emit, is_contact, presence_allowed, user_public
from ..schemas import (
    PrivacyUpdate,
    UserPrivate,
    UserPublic,
    UserUpdate,
)

async def emit_profile_update(db, runtime, user) -> None:
    """Everyone who shows this profile gets a fresh copy: contacts and chat partners."""
    contacts = (await db.execute(
        select(Contact.owner_id).where(Contact.contact_id == user.id)
    )).scalars().all()
    partners = (await db.execute(
        select(ChatMember.user_id).where(
            ChatMember.chat_id.in_(select(ChatMember.chat_id).where(ChatMember.user_id == user.id)),
            ChatMember.user_id != user.id,
            ChatMember.left_at.is_(None),
        ).distinct()
    )).scalars().all()
    recipients = list({*contacts, *partners})
    if recipients:
        await emit(runtime, recipients, "profile.updated", {
            "user_id": str(user.id),
            "username": user.username,
            "display_name": user.display_name,
            "avatar_file_id": str(user.avatar_file_id) if user.avatar_file_id else None,
            "bio": user.bio,
        })


router = APIRouter(prefix="/users", tags=["users"])


@router.get("/me", response_model=UserPrivate)
async def get_me(user: CurrentUser):
    return user


@router.patch("/me", response_model=UserPrivate)
async def update_me(payload: UserUpdate, user: CurrentUser, db: DbDep, runtime: RuntimeDep):
    if payload.display_name is not None:
        user.display_name = payload.display_name
    if payload.bio is not None:
        user.bio = payload.bio
    if payload.avatar_file_id is not None:
        user.avatar_file_id = payload.avatar_file_id
    if payload.settings is not None:
        merged = dict(user.settings or {})
        merged.update(payload.settings)
        user.settings = merged
    await db.commit()
    await db.refresh(user)
    await emit_profile_update(db, runtime, user)
    return user


@router.patch("/me/privacy", response_model=UserPrivate)
async def update_privacy(payload: PrivacyUpdate, user: CurrentUser, db: DbDep):
    if payload.last_seen_privacy is not None:
        user.last_seen_privacy = payload.last_seen_privacy
    if payload.search_privacy is not None:
        user.search_privacy = payload.search_privacy
    await db.commit()
    await db.refresh(user)
    return user


@router.get("/search", response_model=list[UserPublic])
async def search_users(
    user: CurrentUser,
    db: DbDep,
    q: str = Query(min_length=1, max_length=64),
    limit: int = Query(default=20, ge=1, le=50),
):
    pattern = f"%{q.strip()}%"
    result = await db.execute(
        select(User)
        .where(
            User.id != user.id,
            User.is_active.is_(True),
            # Nobody-hidden accounts are never listed, not even by exact username.
            User.search_privacy != "nobody",
            or_(User.username.ilike(pattern), User.display_name.ilike(pattern)),
        )
        .order_by(User.username)
        .limit(limit)
    )
    visible = []
    for candidate in result.scalars():
        if candidate.search_privacy == "contacts" and not (
                await is_contact(db, candidate.id, user.id)
                or await is_contact(db, user.id, candidate.id)):
            continue
        visible.append(await user_public(db, candidate, user.id))
    return visible


@router.get("/blocked", response_model=list[UserPublic])
async def list_blocked(user: CurrentUser, db: DbDep):
    result = await db.execute(
        select(User)
        .join(BlockedUser, BlockedUser.blocked_id == User.id)
        .where(BlockedUser.owner_id == user.id)
    )
    return list(result.scalars())


@router.post("/{user_id}/block", status_code=204)
async def block_user(user_id: uuid.UUID, user: CurrentUser, db: DbDep):
    if user_id == user.id:
        raise HTTPException(status_code=400, detail="Cannot block yourself")
    target = await db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")
    existing = await db.execute(
        select(BlockedUser).where(BlockedUser.owner_id == user.id, BlockedUser.blocked_id == user_id)
    )
    if existing.scalar_one_or_none() is None:
        db.add(BlockedUser(owner_id=user.id, blocked_id=user_id))
        await db.commit()
    return None


@router.delete("/{user_id}/block", status_code=204)
async def unblock_user(user_id: uuid.UUID, user: CurrentUser, db: DbDep):
    await db.execute(
        delete(BlockedUser).where(BlockedUser.owner_id == user.id, BlockedUser.blocked_id == user_id)
    )
    await db.commit()
    return None


@router.get("/{user_id}", response_model=UserPublic)
async def get_user(user_id: uuid.UUID, user: CurrentUser, db: DbDep, runtime: RuntimeDep):
    target = await db.get(User, user_id)
    if target is None or not target.is_active:
        raise HTTPException(status_code=404, detail="User not found")

    # Privacy: a blocked contact must not leak presence information.
    blocked = await db.execute(
        select(func.count()).select_from(BlockedUser).where(
            or_(
                (BlockedUser.owner_id == target.id) & (BlockedUser.blocked_id == user.id),
                (BlockedUser.owner_id == user.id) & (BlockedUser.blocked_id == target.id),
            )
        )
    )
    hide_presence = bool(blocked.scalar_one())
    payload = await user_public(db, target, user.id,
                                online_ids=set(runtime.manager.online_user_ids()))
    if hide_presence:
        # A block always wins over the privacy setting.
        payload = {**payload, "last_seen_at": None, "is_online": False, "presence_visible": False}
    return payload