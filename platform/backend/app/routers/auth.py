"""Authentication: registration, login, refresh-token rotation, session management."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import select

from ..deps import CurrentUser, DbDep, RuntimeDep
from ..models import Device, SecurityEvent, Session as DeviceSession, User
from ..ratelimit import client_ip
from ..schemas import (
    LoginRequest,
    RefreshRequest,
    RegisterRequest,
    SessionOut,
    TokenPair,
    UserPrivate,
)
from ..security import (
    create_access_token,
    hash_password,
    hash_refresh_token,
    new_refresh_token,
    refresh_expiry,
    verify_password,
)

router = APIRouter(prefix="/auth", tags=["auth"])


def _user_agent(request: Request) -> str | None:
    agent = request.headers.get("user-agent")
    return agent[:255] if agent else None


async def _get_or_create_device(db, user_id: uuid.UUID, name: str, platform: str) -> Device:
    result = await db.execute(
        select(Device).where(
            Device.user_id == user_id, Device.name == name, Device.platform == platform
        )
    )
    device = result.scalar_one_or_none()
    if device is None:
        device = Device(user_id=user_id, name=name, platform=platform)
        db.add(device)
        await db.flush()
    else:
        device.last_active_at = datetime.now(UTC)
    return device


async def _issue_tokens(
    db,
    runtime,
    user: User,
    device: Device,
    request: Request,
    family_id: uuid.UUID | None = None,
) -> tuple[TokenPair, DeviceSession]:
    plaintext, digest = new_refresh_token()
    session = DeviceSession(
        user_id=user.id,
        device_id=device.id,
        refresh_token_hash=digest,
        family_id=family_id or uuid.uuid4(),
        expires_at=refresh_expiry(runtime.settings),
        ip=client_ip(request),
        user_agent=_user_agent(request),
    )
    db.add(session)
    await db.flush()
    access_token, expires_in = create_access_token(runtime.settings, user.id, session.id)
    await db.commit()
    return (
        TokenPair(access_token=access_token, refresh_token=plaintext, expires_in=expires_in),
        session,
    )


async def _security_event(db, user_id: uuid.UUID | None, event_type: str, request: Request,
                          meta: dict | None = None) -> None:
    db.add(
        SecurityEvent(
            user_id=user_id,
            type=event_type,
            ip=client_ip(request),
            user_agent=_user_agent(request),
            meta=meta or {},
        )
    )
    await db.commit()


@router.post("/register", response_model=TokenPair, status_code=status.HTTP_201_CREATED)
async def register(payload: RegisterRequest, request: Request, db: DbDep, runtime: RuntimeDep):
    await runtime.login_limiter.enforce(f"register:{client_ip(request)}")

    existing = await db.execute(select(User.id).where(User.username == payload.username))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="Username is already taken")

    user = User(
        username=payload.username,
        display_name=payload.display_name,
        password_hash=hash_password(payload.password),
        settings={},
    )
    db.add(user)
    await db.flush()
    device = await _get_or_create_device(db, user.id, payload.device_name, payload.platform)
    tokens, _ = await _issue_tokens(db, runtime, user, device, request)
    await _security_event(db, user.id, "account.registered", request)
    return tokens


@router.post("/login", response_model=TokenPair)
async def login(payload: LoginRequest, request: Request, db: DbDep, runtime: RuntimeDep):
    await runtime.login_limiter.enforce(f"login:{client_ip(request)}:{payload.username}")

    result = await db.execute(select(User).where(User.username == payload.username))
    user = result.scalar_one_or_none()
    if user is None or not verify_password(user.password_hash, payload.password):
        await _security_event(db, user.id if user else None, "auth.failed", request,
                              {"username": payload.username})
        raise HTTPException(status_code=401, detail="Invalid username or password")
    if user.is_blocked:
        await _security_event(db, user.id, "auth.blocked", request)
        raise HTTPException(status_code=403, detail="Account is blocked")

    if user.suspended_until is not None:
        until = user.suspended_until if user.suspended_until.tzinfo else user.suspended_until.replace(
            tzinfo=UTC
        )
        if until > datetime.now(UTC):
            await _security_event(db, user.id, "auth.suspended", request)
            raise HTTPException(status_code=403, detail="Account is suspended")

    device = await _get_or_create_device(db, user.id, payload.device_name, payload.platform)
    tokens, _ = await _issue_tokens(db, runtime, user, device, request)
    await _security_event(db, user.id, "auth.login", request, {"device": payload.device_name})
    return tokens


@router.post("/refresh", response_model=TokenPair)
async def refresh(payload: RefreshRequest, request: Request, db: DbDep, runtime: RuntimeDep):
    digest = hash_refresh_token(payload.refresh_token)
    result = await db.execute(select(DeviceSession).where(DeviceSession.refresh_token_hash == digest))
    session = result.scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    now = datetime.now(UTC)
    expires_at = session.expires_at if session.expires_at.tzinfo else session.expires_at.replace(
        tzinfo=UTC
    )

    if session.revoked_at is not None:
        # Reuse of a rotated token: treat the whole family as compromised.
        await _revoke_family(db, session.family_id)
        await _security_event(db, session.user_id, "auth.refresh_reuse", request,
                              {"family_id": str(session.family_id)})
        raise HTTPException(status_code=401, detail="Refresh token reuse detected")

    if expires_at < now:
        raise HTTPException(status_code=401, detail="Refresh token expired")

    user = await db.get(User, session.user_id)
    if user is None or user.is_blocked:
        raise HTTPException(status_code=401, detail="Account is not available")

    device = await db.get(Device, session.device_id) if session.device_id else None
    if device is None:
        device = await _get_or_create_device(db, user.id, "Unknown device", "android")

    session.revoked_at = now
    tokens, new_session = await _issue_tokens(db, runtime, user, device, request,
                                              family_id=session.family_id)
    session.replaced_by = new_session.id
    await db.commit()
    await _security_event(db, user.id, "auth.refresh", request)
    return tokens


async def _revoke_family(db, family_id: uuid.UUID) -> None:
    result = await db.execute(select(DeviceSession).where(DeviceSession.family_id == family_id))
    now = datetime.now(UTC)
    for session in result.scalars():
        if session.revoked_at is None:
            session.revoked_at = now
    await db.commit()


@router.post("/logout", status_code=204)
async def logout(request: Request, user: CurrentUser, db: DbDep, runtime: RuntimeDep):
    from ..security import decode_access_token

    token = request.headers["authorization"].split(" ", 1)[1].strip()
    payload = decode_access_token(runtime.settings, token)
    session = await db.get(DeviceSession, uuid.UUID(payload["sid"]))
    if session is not None and session.revoked_at is None:
        session.revoked_at = datetime.now(UTC)
        await db.commit()
    await _security_event(db, user.id, "auth.logout", request)
    return None


@router.get("/sessions", response_model=list[SessionOut])
async def list_sessions(request: Request, user: CurrentUser, db: DbDep, runtime: RuntimeDep):
    from ..security import decode_access_token

    token = request.headers["authorization"].split(" ", 1)[1].strip()
    current_id = uuid.UUID(decode_access_token(runtime.settings, token)["sid"])

    result = await db.execute(
        select(DeviceSession, Device)
        .outerjoin(Device, Device.id == DeviceSession.device_id)
        .where(DeviceSession.user_id == user.id, DeviceSession.revoked_at.is_(None))
        .order_by(DeviceSession.created_at.desc())
    )
    out = []
    for session, device in result.all():
        out.append(
            SessionOut(
                id=session.id,
                device_name=device.name if device else None,
                platform=device.platform if device else None,
                ip=session.ip,
                created_at=session.created_at,
                last_used_at=session.last_used_at,
                current=session.id == current_id,
            )
        )
    return out


@router.delete("/sessions/{session_id}", status_code=204)
async def revoke_session(session_id: uuid.UUID, request: Request, user: CurrentUser, db: DbDep):
    session = await db.get(DeviceSession, session_id)
    if session is None or session.user_id != user.id:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.revoked_at is None:
        session.revoked_at = datetime.now(UTC)
        await db.commit()
    await _security_event(db, user.id, "auth.session_revoked", request, {"session_id": str(session_id)})
    return None


@router.get("/me", response_model=UserPrivate, tags=["users"])
async def me(user: CurrentUser):
    return user
