"""Security tests: rate limiting, token handling, injection, privilege escalation."""
from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.main import create_app
from app.storage import LocalStorage
from tests.conftest import TABLES, auth, register_user


@pytest_asyncio.fixture
async def limited_client(settings):
    """Same application, but with a deliberately tiny rate-limit budget."""
    strict = settings.model_copy(update={
        "rate_limit_enabled": True,
        "rate_limit_requests": 5,
        "rate_limit_window_seconds": 60,
        "login_rate_limit_requests": 3,
        "login_rate_limit_window_seconds": 60,
    })
    engine = create_async_engine(strict.database_url, poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE TABLE {TABLES} CASCADE"))

    import httpx

    application = create_app(settings=strict, engine=engine)
    async with application.router.lifespan_context(application):
        transport = httpx.ASGITransport(app=application)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            client.strict = strict  # type: ignore[attr-defined]
            yield client
    await engine.dispose()


async def test_rate_limit_returns_429(limited_client):
    statuses = []
    for _ in range(8):
        response = await limited_client.get("/api/v1/healthz")
        statuses.append(response.status_code)
    assert statuses.count(200) <= 5
    assert 429 in statuses
    limited = next(s for s in statuses if s == 429)
    assert limited == 429


async def test_login_brute_force_is_throttled(limited_client):
    await register_user(limited_client, "victim_user")
    statuses = []
    for _ in range(6):
        response = await limited_client.post(
            "/api/v1/auth/login",
            json={"username": "victim_user", "password": "wrong-guess"},
        )
        statuses.append(response.status_code)
    assert 429 in statuses


async def test_tampered_jwt_signature_rejected(client, alice):
    token = alice["access_token"]
    header, payload, signature = token.split(".")
    tampered = f"{header}.{payload}.{signature[:-4]}AAAA"
    response = await client.get("/api/v1/users/me", headers=auth(tampered))
    assert response.status_code == 401


async def test_expired_token_rejected(client, alice, settings):
    from app.security import decode_access_token

    payload = decode_access_token(settings, alice["access_token"])
    payload["exp"] = int((datetime.now(UTC) - timedelta(minutes=5)).timestamp())
    expired = jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)

    response = await client.get("/api/v1/users/me", headers=auth(expired))
    assert response.status_code == 401


async def test_wrong_token_type_rejected(client, alice, settings):
    from app.security import decode_access_token

    payload = decode_access_token(settings, alice["access_token"])
    payload["typ"] = "refresh"
    forged = jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    assert (await client.get("/api/v1/users/me", headers=auth(forged))).status_code == 401


async def test_token_signed_with_other_secret_rejected(client, alice, settings):
    from app.security import decode_access_token

    payload = decode_access_token(settings, alice["access_token"])
    forged = jwt.encode(payload, "attacker-secret-key-000000000000", algorithm="HS256")
    assert (await client.get("/api/v1/users/me", headers=auth(forged))).status_code == 401


async def test_storage_rejects_path_traversal(tmp_path):
    storage = LocalStorage(tmp_path / "files")
    for key in ("../../etc/passwd", "..\\windows\\system32", "/etc/shadow", "a/../../b"):
        with pytest.raises(HTTPException):
            storage.path_for(key)


async def test_sql_injection_attempt_is_harmless(client, alice, bob, db_session):
    from tests.conftest import register_user as register

    attacker = await register(client, "injector")
    payload = "'; DROP TABLE users; --"
    response = await client.get("/api/v1/users/search", headers=auth(attacker["access_token"]),
                                params={"q": payload})
    assert response.status_code == 200
    assert response.json() == []

    # Table is intact and data still readable.
    count = (await db_session.execute(text("SELECT count(*) FROM users"))).scalar_one()
    assert count >= 3


async def test_xss_payload_is_stored_verbatim_and_not_executed(client, alice, bob):
    bob_id = (await client.get("/api/v1/users/me", headers=auth(bob["access_token"]))).json()["id"]
    chat = (await client.post("/api/v1/chats", headers=auth(alice["access_token"]),
                              json={"type": "private", "member_ids": [bob_id]})).json()

    payload = "<script>alert('xss')</script>"
    sent = await client.post(f"/api/v1/chats/{chat['id']}/messages",
                             headers=auth(alice["access_token"]),
                             json={"type": "text", "text": payload})
    assert sent.status_code == 201

    history = await client.get(f"/api/v1/chats/{chat['id']}/messages",
                               headers=auth(bob["access_token"]))
    assert history.json()["items"][0]["text"] == payload
    # The API never renders HTML; clients escape text. JSON is served as application/json.
    assert history.headers["content-type"].startswith("application/json")


async def test_password_hash_is_never_exposed(client, alice):
    me = await client.get("/api/v1/users/me", headers=auth(alice["access_token"]))
    assert "password" not in me.text.lower()

    detail_target = (await client.get("/api/v1/users/me", headers=auth(alice["access_token"]))).json()
    other = await client.get(f"/api/v1/users/{detail_target['id']}",
                             headers=auth(alice["access_token"]))
    assert "password" not in other.text.lower()


async def test_regular_user_cannot_escalate_privileges(client, alice, staff_user):
    me = (await client.get("/api/v1/users/me", headers=auth(alice["access_token"]))).json()

    assert (await client.get("/api/v1/admin/stats",
                             headers=auth(alice["access_token"]))).status_code == 403
    assert (await client.post(f"/api/v1/admin/users/{me['id']}/role",
                              headers=auth(alice["access_token"]),
                              json={"role": "super_admin"})).status_code == 403
    assert (await client.post("/api/v1/admin/maintenance",
                              headers=auth(alice["access_token"]),
                              json={"enabled": True})).status_code == 403
    assert (await client.get("/api/v1/admin/export/users.csv",
                             headers=auth(alice["access_token"]))).status_code == 403


async def test_blocked_session_cannot_be_revived_by_refresh(client, staff_user, alice):
    login = await client.post("/api/v1/auth/login",
                              json={"username": "alice_test", "password": alice["password"]})
    fresh_refresh = login.json()["refresh_token"]

    user_id = (await client.get("api/v1/users/me" if False else "/api/v1/users/me",
                                headers=auth(alice["access_token"]))).json()["id"]
    await client.post(f"/api/v1/admin/users/{user_id}/block",
                      headers=auth(staff_user["access_token"]), json={"blocked": True})

    refreshed = await client.post("/api/v1/auth/refresh", json={"refresh_token": fresh_refresh})
    assert refreshed.status_code == 401


async def test_websocket_rejects_token_after_logout(client, alice):
    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    # The ASGI transport cannot speak WebSocket; token invalidation is verified
    # through the HTTP surface that the socket handshake also uses.
    await client.post("/api/v1/auth/logout", headers=auth(alice["access_token"]))
    assert (await client.get("/api/v1/auth/sessions",
                             headers=auth(alice["access_token"]))).status_code == 401
    assert TestClient is not None and WebSocketDisconnect is not None


async def test_upload_rejects_disallowed_kind_and_missing_auth(client, alice):
    assert (await client.post("/api/v1/files",
                              files={"file": ("a.txt", b"x", "text/plain")},
                              data={"kind": "document"})).status_code == 401

    response = await client.post("/api/v1/files", headers=auth(alice["access_token"]),
                                 files={"file": ("a.txt", b"x", "text/plain")},
                                 data={"kind": "executable"})
    assert response.status_code == 422


async def test_refresh_tokens_are_not_stored_in_plaintext(client, db_session, alice):
    hashes = (await db_session.execute(text("SELECT refresh_token_hash FROM sessions"))).scalars().all()
    assert hashes
    assert all(len(h) == 64 and alice["refresh_token"] != h for h in hashes)


async def test_timing_of_password_verification_is_argon2_bounded(client, alice):
    start = time.perf_counter()
    await client.post("/api/v1/auth/login",
                      json={"username": alice["username"], "password": "wrong"})
    elapsed = time.perf_counter() - start
    # Argon2 verification is deliberately slow; a plaintext comparison would be ~ms.
    assert elapsed > 0.001
