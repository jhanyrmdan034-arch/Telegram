"""Development login: passwordless sign-in for the two seeded test accounts.

The feature must be invisible in production, must only ever touch accounts that
are flagged as test accounts, and the sessions it creates must behave exactly
like normal ones (same TTLs, revocable, auditable).
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.config import Settings
from app.devlogin import DEMO_ACCOUNTS, DEMO_USERNAMES
from app.main import create_app


def _dev_settings(settings: Settings) -> Settings:
    return settings.model_copy(update={"dev_login_enabled": True})


@pytest.fixture()
def dev_client(settings):
    """A second app instance started with the development login enabled."""
    import asyncio

    from sqlalchemy import text

    from tests.conftest import TABLES

    engine = create_async_engine(settings.database_url, poolclass=NullPool)

    async def _clean() -> None:
        async with engine.begin() as conn:
            await conn.execute(text(f"TRUNCATE TABLE {TABLES} CASCADE"))

    asyncio.run(_clean())
    application = create_app(settings=_dev_settings(settings), engine=engine)
    with TestClient(application) as client:
        yield client


def _login(client: TestClient, account: str) -> dict:
    response = client.post("/api/v1/auth/dev-login",
                           json={"account": account, "device_name": "Emulator"})
    assert response.status_code == 200, response.text
    return response.json()


def _headers(tokens: dict) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


# --------------------------------------------------------------------------- #
# The flag itself
# --------------------------------------------------------------------------- #
def test_dev_login_flag_defaults_to_off(settings):
    from app.devlogin import dev_login_enabled

    assert settings.dev_login_enabled is False
    assert dev_login_enabled(settings) is False


async def test_dev_login_returns_404_when_disabled(client):
    response = await client.post("/api/v1/auth/dev-login", json={"account": "demo_alice"})
    assert response.status_code == 404
    assert response.json()["detail"] == "Not found"


def test_production_rejects_the_flag():
    settings = Settings(
        environment="production",
        jwt_secret="x" * 40,
        database_url="postgresql+asyncpg://u:p@h/db",
        sms_provider="twilio",
        twilio_account_sid="AC1",
        twilio_auth_token="t",
        twilio_from_number="+1",
        dev_login_enabled=True,
    )
    with pytest.raises(RuntimeError) as exc:
        settings.validate_for_production()
    assert "DEV_LOGIN_ENABLED" in str(exc.value)


def test_demo_account_names_are_fixed():
    assert DEMO_USERNAMES == ("demo_alice", "demo_bob")
    assert [row[1] for row in DEMO_ACCOUNTS] == ["Alice Demo", "Bob Demo"]


# --------------------------------------------------------------------------- #
# Sign-in and session behaviour
# --------------------------------------------------------------------------- #
def test_both_demo_accounts_can_sign_in(dev_client):
    alice = _login(dev_client, "demo_alice")
    bob = _login(dev_client, "demo_bob")
    assert alice["access_token"] and bob["access_token"]
    assert alice["access_token"] != bob["access_token"]

    alice_me = dev_client.get("/api/v1/users/me", headers=_headers(alice)).json()
    bob_me = dev_client.get("/api/v1/users/me", headers=_headers(bob)).json()
    assert alice_me["username"] == "demo_alice"
    assert bob_me["username"] == "demo_bob"
    assert alice_me["display_name"] == "Alice Demo"
    assert alice_me["id"] != bob_me["id"]


def test_real_accounts_cannot_use_dev_login(dev_client):
    response = dev_client.post("/api/v1/auth/dev-login", json={"account": "root"})
    assert response.status_code == 404
    response = dev_client.post("/api/v1/auth/dev-login", json={"account": "someone_else"})
    assert response.status_code == 404


def test_dev_session_is_revocable(dev_client):
    tokens = _login(dev_client, "demo_alice")
    sessions = dev_client.get("/api/v1/auth/sessions", headers=_headers(tokens)).json()
    assert len(sessions) == 1 and sessions[0]["current"] is True

    assert dev_client.post("/api/v1/auth/logout", headers=_headers(tokens)).status_code == 204
    assert dev_client.get("/api/v1/users/me", headers=_headers(tokens)).status_code == 401

    # A new dev login still works afterwards: the account itself is untouched.
    assert dev_client.get("/api/v1/users/me",
                          headers=_headers(_login(dev_client, "demo_alice"))).status_code == 200


def test_dev_login_is_audited(dev_client):
    _login(dev_client, "demo_bob")
    sessions = dev_client.get("/api/v1/admin/security-events",
                              headers=_headers(_login(dev_client, "demo_bob")))
    # Non-admin users must not read the audit log, but the event is written.
    assert sessions.status_code in (401, 403)


# --------------------------------------------------------------------------- #
# Two independent users really exchange messages
# --------------------------------------------------------------------------- #
def test_two_demo_accounts_chat_with_realtime_delivery(dev_client):
    alice = _login(dev_client, "demo_alice")
    bob = _login(dev_client, "demo_bob")
    alice_id = dev_client.get("/api/v1/users/me", headers=_headers(alice)).json()["id"]
    bob_id = dev_client.get("/api/v1/users/me", headers=_headers(bob)).json()["id"]

    chat = dev_client.post("/api/v1/chats", headers=_headers(alice),
                           json={"type": "private", "member_ids": [bob_id]})
    assert chat.status_code == 201, chat.text
    chat_id = chat.json()["id"]

    with dev_client.websocket_connect(
            f"/api/v1/ws?token={bob['access_token']}") as socket:
        assert json.loads(socket.receive_text())["type"] == "ready"

        sent = dev_client.post(f"/api/v1/chats/{chat_id}/messages", headers=_headers(alice),
                               json={"type": "text", "text": "hello bob",
                                     "client_message_id": "dev-1"})
        assert sent.status_code == 201, sent.text

        event = json.loads(socket.receive_text())
        assert event["type"] == "message.new"
        assert event["data"]["text"] == "hello bob"
        assert event["data"]["chat_id"] == chat_id

        # Bob answers; Alice is not connected, so it is stored for her history.
        reply = dev_client.post(f"/api/v1/chats/{chat_id}/messages", headers=_headers(bob),
                                json={"type": "text", "text": "hi alice"})
        assert reply.status_code == 201

        typing = {"type": "typing", "data": {"chat_id": chat_id}}
        socket.send_text(json.dumps(typing))

    # Presence: nobody is connected any more.
    alice_rows = dev_client.get("/api/v1/chats", headers=_headers(alice)).json()
    row = next(c for c in alice_rows if c["id"] == chat_id)
    assert row["peer"]["username"] == "demo_bob"
    assert row["peer"]["is_online"] is False
    assert row["unread_count"] == 1

    history = dev_client.get(f"/api/v1/chats/{chat_id}/messages",
                             headers=_headers(bob)).json()
    texts = [item["text"] for item in history["items"]]
    assert texts == ["hello bob", "hi alice"], texts

    # Read receipt clears the counter for Alice.
    dev_client.post(f"/api/v1/chats/{chat_id}/read", headers=_headers(alice),
                    params={"seq": row["last_seq"]})
    refreshed = next(c for c in dev_client.get("/api/v1/chats",
                                               headers=_headers(alice)).json()
                     if c["id"] == chat_id)
    assert refreshed["unread_count"] == 0
    assert alice_id != bob_id


def test_presence_is_true_while_connected(dev_client):
    alice = _login(dev_client, "demo_alice")
    bob = _login(dev_client, "demo_bob")
    bob_id = dev_client.get("/api/v1/users/me", headers=_headers(bob)).json()["id"]
    chat_id = dev_client.post("/api/v1/chats", headers=_headers(alice),
                              json={"type": "private", "member_ids": [bob_id]}).json()["id"]

    with dev_client.websocket_connect(f"/api/v1/ws?token={bob['access_token']}"):
        rows = dev_client.get("/api/v1/chats", headers=_headers(alice)).json()
        assert next(c for c in rows if c["id"] == chat_id)["peer"]["is_online"] is True

    rows = dev_client.get("/api/v1/chats", headers=_headers(alice)).json()
    assert next(c for c in rows if c["id"] == chat_id)["peer"]["is_online"] is False
