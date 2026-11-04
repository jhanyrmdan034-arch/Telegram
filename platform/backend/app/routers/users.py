"""User profiles, discovery, blocking and privacy."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import delete, func, or_, select

from ..deps import CurrentUser, DbDep
from ..models import BlockedUser, User
from ..schemas import UserPrivate, UserPublic, UserUpdate

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/me", response_model=UserPrivate)
async def get_me(user: CurrentUser):
    return user


@router.patch("/me", response_model=UserPrivate)
async def update_me(payload: UserUpdate, user: CurrentUser, db: DbDep):
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
            or_(User.username.ilike(pattern), User.display_name.ilike(pattern)),
        )
        .order_by(User.username)
        .limit(limit)
    )
    return list(result.scalars())


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
async def get_user(user_id: uuid.UUID, user: CurrentUser, db: DbDep):
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
    public = UserPublic.model_validate(target)
    if hide_presence:
        public = public.model_copy(update={"last_seen_at": None})
    return public
