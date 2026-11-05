"""Authentication, session lifecycle and refresh-token rotation."""
from __future__ import annotations

from tests.conftest import auth, register_user


async def test_register_and_login(client):
    tokens = await register_user(client, "newcomer")

    me = await client.get("/api/v1/users/me", headers=auth(tokens["access_token"]))
    assert me.status_code == 200
    assert me.json()["username"] == "newcomer"

    login = await client.post(
        "/api/v1/auth/login",
        json={"username": "newcomer", "password": tokens["password"], "device_name": "second"},
    )
    assert login.status_code == 200
    assert login.json()["access_token"] != tokens["access_token"]


async def test_duplicate_username_rejected(client):
    await register_user(client, "taken_name")
    response = await client.post(
        "/api/v1/auth/register",
        json={"username": "taken_name", "password": "AnotherPass123", "display_name": "Dup"},
    )
    assert response.status_code == 409


async def test_wrong_password_rejected(client, alice):
    response = await client.post(
        "/api/v1/auth/login",
        json={"username": alice["username"], "password": "wrong-password"},
    )
    assert response.status_code == 401
    assert "Invalid" in response.json()["detail"]


async def test_password_is_not_stored_in_plaintext(client, db_session, alice):
    from sqlalchemy import select

    from app.models import User

    user = (await db_session.execute(select(User).where(User.username == alice["username"]))).scalar_one()
    assert alice["password"] not in user.password_hash
    assert user.password_hash.startswith("$argon2")


async def test_refresh_rotation_and_reuse_detection(client, alice):
    first = await client.post("/api/v1/auth/refresh", json={"refresh_token": alice["refresh_token"]})
    assert first.status_code == 200
    rotated = first.json()
    assert rotated["refresh_token"] != alice["refresh_token"]

    # Replaying the rotated (old) token must be rejected and revoke the family.
    replay = await client.post("/api/v1/auth/refresh", json={"refresh_token": alice["refresh_token"]})
    assert replay.status_code == 401
    assert "reuse" in replay.json()["detail"].lower()

    # The token issued from the replay attempt is now revoked as well.
    after = await client.post("/api/v1/auth/refresh", json={"refresh_token": rotated["refresh_token"]})
    assert after.status_code == 401


async def test_sessions_listing_and_revocation(client, alice):
    login = await client.post(
        "/api/v1/auth/login",
        json={"username": alice["username"], "password": alice["password"], "device_name": "laptop",
              "platform": "desktop"},
    )
    assert login.status_code == 200

    sessions = await client.get("/api/v1/auth/sessions", headers=auth(alice["access_token"]))
    assert sessions.status_code == 200
    items = sessions.json()
    assert len(items) >= 2
    assert any(s["current"] for s in items)

    other = next(s for s in items if not s["current"])
    revoke = await client.delete(f"/api/v1/auth/sessions/{other['id']}",
                                headers=auth(alice["access_token"]))
    assert revoke.status_code == 204

    remaining = (await client.get("/api/v1/auth/sessions",
                                  headers=auth(alice["access_token"]))).json()
    assert all(s["id"] != other["id"] for s in remaining)


async def test_logout_invalidates_access_token(client, alice):
    assert (await client.get("/api/v1/users/me", headers=auth(alice["access_token"]))).status_code == 200
    logout = await client.post("/api/v1/auth/logout", headers=auth(alice["access_token"]))
    assert logout.status_code == 204
    assert (await client.get("/api/v1/users/me", headers=auth(alice["access_token"]))).status_code == 401


async def test_protected_endpoint_requires_token(client):
    assert (await client.get("/api/v1/users/me")).status_code == 401
    assert (await client.get("/api/v1/users/me", headers=auth("garbage.token.value"))).status_code == 401


async def test_registration_validation(client):
    response = await client.post(
        "/api/v1/auth/register",
        json={"username": "a", "password": "short", "display_name": "X"},
    )
    assert response.status_code == 422
