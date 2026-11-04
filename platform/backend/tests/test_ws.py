"""End-to-end WebSocket tests against the real ASGI application."""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.main import create_app


@pytest.fixture
def sync_client(settings):
    application = create_app(settings=settings)
    with TestClient(application) as client:
        yield client


def _register(client: TestClient) -> dict:
    username = f"ws_{uuid.uuid4().hex[:8]}"
    challenge = client.post("/api/v1/auth/otp/request",
                            json={"username": username, "purpose": "register"})
    assert challenge.status_code == 202, challenge.text
    response = client.post("/api/v1/auth/register", json={
        "username": username, "password": "WebSocketPass123", "display_name": username,
        "registration_token": challenge.json().get("registration_token"),
    })
    assert response.status_code == 201, response.text
    return response.json()


def _headers(tokens: dict) -> dict:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


def test_websocket_delivers_new_messages(sync_client: TestClient):
    alice, bob = _register(sync_client), _register(sync_client)
    bob_id = sync_client.get("/api/v1/users/me", headers=_headers(bob)).json()["id"]
    chat = sync_client.post("/api/v1/chats", headers=_headers(alice),
                            json={"type": "private", "member_ids": [bob_id]}).json()

    with sync_client.websocket_connect(f"/api/v1/ws?token={alice['access_token']}") as ws:
        ready = ws.receive_json()
        assert ready["type"] == "ready"
        assert ready["data"]["user_id"] == sync_client.get(
            "/api/v1/users/me", headers=_headers(alice)).json()["id"]

        ws.send_json({"type": "ping"})
        assert ws.receive_json()["type"] == "pong"

        sent = sync_client.post(f"/api/v1/chats/{chat['id']}/messages", headers=_headers(bob),
                                json={"type": "text", "text": "live update"})
        assert sent.status_code == 201

        event = ws.receive_json()
        assert event["type"] == "message.new"
        assert event["data"]["text"] == "live update"
        assert event["data"]["seq"] == 1


def test_typing_indicator_propagates(sync_client: TestClient):
    alice, bob = _register(sync_client), _register(sync_client)
    bob_id = sync_client.get("/api/v1/users/me", headers=_headers(bob)).json()["id"]
    chat = sync_client.post("/api/v1/chats", headers=_headers(alice),
                            json={"type": "private", "member_ids": [bob_id]}).json()

    with sync_client.websocket_connect(f"/api/v1/ws?token={alice['access_token']}") as ws:
        assert ws.receive_json()["type"] == "ready"

        # Bob types through the WebSocket interface.
        with sync_client.websocket_connect(f"/api/v1/ws?token={bob['access_token']}") as bob_ws:
            assert bob_ws.receive_json()["type"] == "ready"
            bob_ws.send_json({"type": "typing", "data": {"chat_id": chat["id"]}})

            event = ws.receive_json()
            assert event["type"] == "typing"
            assert event["data"]["chat_id"] == chat["id"]


def test_websocket_rejects_invalid_token(sync_client: TestClient):
    with pytest.raises(WebSocketDisconnect) as excinfo:
        with sync_client.websocket_connect("/api/v1/ws?token=not-a-token") as ws:
            ws.receive_json()
    assert excinfo.value.code == 4401


def test_websocket_rejects_revoked_session(sync_client: TestClient):
    alice = _register(sync_client)
    sync_client.post("/api/v1/auth/logout", headers=_headers(alice))

    with pytest.raises(WebSocketDisconnect) as excinfo:
        with sync_client.websocket_connect(f"/api/v1/ws?token={alice['access_token']}") as ws:
            ws.receive_json()
    assert excinfo.value.code == 4401


def test_websocket_rejects_non_member_chat_access(sync_client: TestClient):
    alice, bob, eve = _register(sync_client), _register(sync_client), _register(sync_client)
    bob_id = sync_client.get("/api/v1/users/me", headers=_headers(bob)).json()["id"]
    chat = sync_client.post("/api/v1/chats", headers=_headers(alice),
                            json={"type": "private", "member_ids": [bob_id]}).json()

    with sync_client.websocket_connect(f"/api/v1/ws?token={eve['access_token']}") as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"type": "typing", "data": {"chat_id": chat["id"]}})
        response = ws.receive_json()
        assert response["type"] == "error"
        assert response["data"]["detail"] == "not a member"
