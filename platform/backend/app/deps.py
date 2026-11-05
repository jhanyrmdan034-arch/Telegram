"""Shared FastAPI dependencies: runtime, database session, authentication, RBAC."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Callable

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Session, User
from .permissions import Permission
from .runtime import Runtime
from .security import decode_access_token

bearer_scheme = HTTPBearer(auto_error=False)

LAST_SEEN_UPDATE_INTERVAL = timedelta(seconds=60)


def get_runtime(request: Request) -> Runtime:
    return request.app.state.runtime


RuntimeDep = Annotated[Runtime, Depends(get_runtime)]


async def get_db(runtime: RuntimeDep) -> AsyncSession:
    async with runtime.session_factory() as session:
        yield session


DbDep = Annotated[AsyncSession, Depends(get_db)]


def _unauthorized(detail: str = "Not authenticated") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_current_user(
    runtime: RuntimeDep,
    db: DbDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)] = None,
) -> User:
    if credentials is None or not credentials.credentials:
        raise _unauthorized()
    try:
        payload = decode_access_token(runtime.settings, credentials.credentials)
        user_id = uuid.UUID(payload["sub"])
        session_id = uuid.UUID(payload["sid"])
    except (jwt.PyJWTError, KeyError, ValueError):
        raise _unauthorized("Invalid or expired token")

    db_session = await db.get(Session, session_id)
    now = datetime.now(UTC)
    if db_session is None or db_session.revoked_at is not None or db_session.user_id != user_id:
        raise _unauthorized("Session is no longer valid")
    expires_at = db_session.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if expires_at < now:
        raise _unauthorized("Session expired")

    user = await db.get(User, user_id)
    if user is None:
        raise _unauthorized("Account not found")
    if user.is_blocked:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account is blocked")
    if user.suspended_until is not None:
        suspended_until = user.suspended_until
        if suspended_until.tzinfo is None:
            suspended_until = suspended_until.replace(tzinfo=UTC)
        if suspended_until > now:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Account is suspended",
            )

    # Refresh presence lazily so hot paths do not write on every request.
    if user.last_seen_at is None or (now - _aware(user.last_seen_at)) > LAST_SEEN_UPDATE_INTERVAL:
        user.last_seen_at = now
        db_session.last_used_at = now
        await db.commit()
        await db.refresh(user)

    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


async def get_current_session(
    runtime: RuntimeDep,
    db: DbDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)] = None,
) -> Session:
    user = await get_current_user(runtime, db, credentials)
    payload = decode_access_token(runtime.settings, credentials.credentials)  # type: ignore[union-attr]
    session = await db.get(Session, uuid.UUID(payload["sid"]))
    assert session is not None
    return session


def permissions_of(user: User) -> set[str]:
    if user.admin_role is None:
        return set()
    return set(user.admin_role.permissions or [])


def is_staff(user: User) -> bool:
    return user.admin_role is not None


def require_permission(*required: Permission | str) -> Callable:
    """Dependency factory. Super admins implicitly hold every permission."""
    wanted = {p.value if isinstance(p, Permission) else p for p in required}

    async def dependency(user: CurrentUser) -> User:
        held = permissions_of(user)
        if user.admin_role is not None and user.admin_role.name == "super_admin":
            return user
        if not wanted.issubset(held):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions",
            )
        return user

    return dependency


async def load_user_by_id(db: AsyncSession, user_id: uuid.UUID) -> User:
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return user
