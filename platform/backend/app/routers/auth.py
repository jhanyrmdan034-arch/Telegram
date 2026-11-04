"""Authentication: registration, login, refresh-token rotation, session management."""
from __future__ import annotations

import hashlib
import logging
import hmac
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import select, update

from ..deps import CurrentUser, DbDep, RuntimeDep
from ..devlogin import dev_login_enabled, find_demo_user
from ..models import (
    Device, OtpCode, RegistrationGrant, SecurityEvent, Session as DeviceSession, User,
)
from ..ratelimit import client_ip
from ..notifications import build_sms_sender
from ..schemas import (
    LoginRequest,
    DevLoginRequest,
    OtpChallenge,
    OtpRequest,
    OtpVerifyRequest,
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

log = logging.getLogger("messenger.auth")
router = APIRouter(prefix="/auth", tags=["auth"])


def _otp_digest(secret: str, username: str, code: str) -> str:
    """Codes are stored as HMAC digests — a database leak must not reveal valid codes."""
    return hmac.new(secret.encode(), f"{username}:{code}".encode(), hashlib.sha256).hexdigest()


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

    grant: RegistrationGrant | None = None
    if runtime.settings.require_phone_verification:
        if not payload.registration_token:
            raise HTTPException(
                status_code=403,
                detail="Phone number is not verified: request a verification code first",
            )
        grant = (await db.execute(
            select(RegistrationGrant).where(RegistrationGrant.token == payload.registration_token)
        )).scalar_one_or_none()
        if grant is None or grant.username != payload.username:
            raise HTTPException(status_code=403, detail="Invalid registration token")
        if grant.used_at is not None:
            raise HTTPException(status_code=409, detail="Registration token already used")
        if grant.expires_at <= datetime.now(UTC):
            raise HTTPException(status_code=403,
                                detail="Registration token expired, request a new code")

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

    if grant is not None:
        grant.used_at = datetime.now(UTC)
        # The code was spent on the sign-up itself, so it cannot also be used to sign in.
        await db.execute(
            update(OtpCode)
            .where(OtpCode.username == payload.username, OtpCode.consumed_at.is_(None))
            .values(consumed_at=datetime.now(UTC))
        )
        await db.commit()

    await _security_event(db, user.id, "account.registered", request,
                          {"phone_verified": grant is not None})
    return tokens


@router.post("/dev-login", response_model=TokenPair)
async def dev_login(payload: DevLoginRequest, request: Request, db: DbDep, runtime: RuntimeDep):
    """Development-only passwordless sign-in for a seeded test account.

    Returns 404 — not 403 — when the feature is off, so a production deployment
    does not even reveal that the route exists. The session it creates is an
    ordinary one: same token TTLs, same revocation, same audit trail.
    """
    if not dev_login_enabled(runtime.settings):
        raise HTTPException(status_code=404, detail="Not found")
    await runtime.login_limiter.enforce(f"dev-login:{client_ip(request)}")

    user = await find_demo_user(db, payload.account)
    if user is None:
        raise HTTPException(status_code=404, detail="Unknown test account")
    if user.is_blocked:
        raise HTTPException(status_code=403, detail="Account is blocked")

    device = await _get_or_create_device(db, user.id, payload.device_name,
                                         payload.platform)
    tokens, _ = await _issue_tokens(db, runtime, user, device, request)
    await _security_event(db, user.id, "auth.dev_login", request,
                          {"account": payload.account, "environment": runtime.settings.environment})
    log.warning("DEV LOGIN used for %s from %s", payload.account, client_ip(request))
    return tokens


@router.post("/otp/request", response_model=OtpChallenge, status_code=202)
async def request_otp(payload: OtpRequest, request: Request, db: DbDep, runtime: RuntimeDep):
    await runtime.login_limiter.enforce(f"otp:{client_ip(request)}:{payload.username}")

    user = (await db.execute(select(User).where(User.username == payload.username))).scalar_one_or_none()
    if payload.purpose == "login" and user is None:
        # Explicit rather than silent: the client shows "account not found" and
        # offers the registration screen instead of hanging on an SMS that never comes.
        raise HTTPException(status_code=404, detail="Account not found")
    if user is not None and user.is_blocked:
        raise HTTPException(status_code=403, detail="Account is blocked")

    now = datetime.now(UTC)
    # Invalidate any outstanding code so only the newest one can be used.
    await db.execute(
        update(OtpCode)
        .where(OtpCode.username == payload.username, OtpCode.consumed_at.is_(None))
        .values(consumed_at=now)
    )

    code = f"{secrets.randbelow(1_000_000):06d}"
    expires_at = now + timedelta(seconds=runtime.settings.otp_ttl_seconds)
    record = OtpCode(
        username=payload.username,
        code_hash=_otp_digest(runtime.settings.jwt_secret, payload.username, code),
        purpose=payload.purpose,
        expires_at=expires_at,
    )
    db.add(record)

    registration_token = None
    if payload.purpose == "register" and user is None:
        # The code went to the phone; this grant lets exactly one registration
        # through for that number, and only until the code expires.
        registration_token = secrets.token_urlsafe(32)
        db.add(RegistrationGrant(
            username=payload.username, token=registration_token, expires_at=expires_at,
        ))
    await db.commit()

    sender = build_sms_sender(runtime.settings)
    try:
        await sender.send(payload.username, f"Messenger verification code: {code}")
    except Exception as exc:  # noqa: BLE001 - any gateway failure is a delivery failure
        record.consumed_at = datetime.now(UTC)
        await db.commit()
        log.warning("OTP delivery failed via %s: %s", sender.name, exc)
        raise HTTPException(status_code=502, detail="Verification code could not be delivered")
    await _security_event(db, user.id if user else None, "auth.otp_requested", request,
                          {"purpose": payload.purpose})

    return OtpChallenge(
        delivery="log" if sender.name == "log" else "sms",
        expires_in=runtime.settings.otp_ttl_seconds,
        debug_code=code if runtime.settings.otp_debug else None,
        registration_token=registration_token,
    )


@router.post("/otp/verify", response_model=TokenPair)
async def verify_otp(payload: OtpVerifyRequest, request: Request, db: DbDep,
                     runtime: RuntimeDep):
    await runtime.login_limiter.enforce(f"otp-verify:{client_ip(request)}:{payload.username}")
    now = datetime.now(UTC)

    record = (await db.execute(
        select(OtpCode)
        .where(
            OtpCode.username == payload.username,
            OtpCode.consumed_at.is_(None),
            OtpCode.expires_at > now,
        )
        .order_by(OtpCode.created_at.desc())
    )).scalars().first()

    if record is None:
        raise HTTPException(status_code=401, detail="Code expired, request a new one")
    if record.attempts >= runtime.settings.otp_max_attempts:
        raise HTTPException(status_code=429, detail="Too many incorrect attempts")

    expected = _otp_digest(runtime.settings.jwt_secret, payload.username, payload.code)
    if not hmac.compare_digest(record.code_hash, expected):
        record.attempts += 1
        await db.commit()
        raise HTTPException(status_code=401, detail="Incorrect code")

    record.consumed_at = now
    await db.commit()

    user = (await db.execute(select(User).where(User.username == payload.username))).scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="Complete registration to continue")
    if user.is_blocked:
        raise HTTPException(status_code=403, detail="Account is blocked")

    device = await _get_or_create_device(db, user.id, payload.device_name, payload.platform)
    tokens, _ = await _issue_tokens(db, runtime, user, device, request)
    await _security_event(db, user.id, "auth.otp_login", request,
                          {"device": payload.device_name})
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
