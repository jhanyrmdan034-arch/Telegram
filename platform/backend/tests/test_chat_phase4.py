"""Phase 4 — the chat screen contract: history, send, reply, edit, delete,
typing, search, idempotency and catch-up after a reconnect.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture
def sync_client(settings):
    application = create_app(settings=settings)
    with TestClient(application) as client:
        yield client


def _register(client: TestClient) -> dict:
    username = f"chat_{uuid.uuid4().hex[:8]}"
    challenge = client.post("/api/v1/auth/otp/request",
                            json={"username": username, "purpose": "register"})
    assert challenge.status_code == 202, challenge.text
    response = client.post("/api/v1/auth/register", json={
        "username": username, "password": "ChatPass1234", "display_name": username,
        "registration_token": challenge.json().get("registration_token"),
    })
    assert response.status_code == 201, response.text
    return response.json()


def _h(tokens: dict) -> dict:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


def _chat(client: TestClient, alice: dict, bob: dict) -> dict:
    bob_id = client.get("/api/v1/users/me", headers=_h(bob)).json()["id"]
    response = client.post("/api/v1/chats", headers=_h(alice),
                           json={"type": "private", "member_ids": [bob_id]})
    assert response.status_code == 201, response.text
    return response.json()


def _send(client: TestClient, tokens: dict, chat_id: str, text: str, **extra) -> dict:
    response = client.post(f"/api/v1/chats/{chat_id}/messages", headers=_h(tokens),
                           json={"text": text, **extra})
    assert response.status_code == 201, response.text
    return response.json()


def test_history_pages_both_directions(sync_client: TestClient):
    alice, bob = _register(sync_client), _register(sync_client)
    chat = _chat(sync_client, alice, bob)
    for index in range(7):
        _send(sync_client, alice, chat["id"], f"m{index}")

    first = sync_client.get(f"/api/v1/chats/{chat['id']}/messages", headers=_h(bob),
                            params={"limit": 3}).json()
    assert [m["text"] for m in first["items"]] == ["m4", "m5", "m6"]
    assert first["next_before_seq"]

    older = sync_client.get(f"/api/v1/chats/{chat['id']}/messages", headers=_h(bob),
                            params={"limit": 3, "before_seq": first["next_before_seq"]}).json()
    assert [m["text"] for m in older["items"]] == ["m1", "m2", "m3"]

    # forward paging = catching up after a reconnect
    newer = sync_client.get(f"/api/v1/chats/{chat['id']}/messages", headers=_h(bob),
                            params={"after_seq": 5}).json()
    assert [m["text"] for m in newer["items"]] == ["m5", "m6"]


def test_reply_carries_its_parent_and_sender(sync_client: TestClient):
    alice, bob = _register(sync_client), _register(sync_client)
    chat = _chat(sync_client, alice, bob)
    root = _send(sync_client, alice, chat["id"], "سؤال")
    reply = _send(sync_client, bob, chat["id"], "جواب", reply_to_id=root["id"])

    assert reply["reply_to_id"] == root["id"]
    assert reply["sender_id"] != root["sender_id"]
    for message in (root, reply):
        assert message["created_at"] and message["seq"] > 0


def test_edit_and_delete_broadcast_events(sync_client: TestClient):
    alice, bob = _register(sync_client), _register(sync_client)
    chat = _chat(sync_client, alice, bob)
    message = _send(sync_client, alice, chat["id"], "قبل")

    with sync_client.websocket_connect(f"/api/v1/ws?token={bob['access_token']}") as socket:
        assert socket.receive_json()["type"] == "ready"
        edited = sync_client.patch(f"/api/v1/messages/{message['id']}", headers=_h(alice),
                                   json={"text": "بعد"})
        assert edited.status_code == 200, edited.text
        assert edited.json()["text"] == "بعد" and edited.json()["edited_at"]

        deleted = sync_client.delete(f"/api/v1/messages/{message['id']}", headers=_h(alice))
        assert deleted.status_code == 204

        seen = []
        for _ in range(10):
            try:
                frame = socket.receive_json()
            except Exception:
                break
            seen.append(frame["type"])
            if "message.deleted" in seen:
                break
        assert "message.edited" in seen, seen
        assert "message.deleted" in seen, seen

    history = sync_client.get(f"/api/v1/chats/{chat['id']}/messages", headers=_h(bob)).json()
    assert all(m["id"] != message["id"] for m in history["items"])


def test_typing_reaches_the_other_side(sync_client: TestClient):
    alice, bob = _register(sync_client), _register(sync_client)
    chat = _chat(sync_client, alice, bob)

    with sync_client.websocket_connect(f"/api/v1/ws?token={alice['access_token']}") as socket:
        assert socket.receive_json()["type"] == "ready"
        assert sync_client.post(f"/api/v1/chats/{chat['id']}/typing",
                                headers=_h(bob)).status_code == 204
        for _ in range(8):
            frame = socket.receive_json()
            if frame["type"] == "typing":
                assert frame["data"]["chat_id"] == chat["id"]
                break
        else:
            pytest.fail("no typing event received")


def test_search_finds_only_matching_messages(sync_client: TestClient):
    alice, bob = _register(sync_client), _register(sync_client)
    chat = _chat(sync_client, alice, bob)
    _send(sync_client, alice, chat["id"], "قرار فردا ساعت پنج")
    _send(sync_client, alice, chat["id"], "سلام")

    found = sync_client.get(f"/api/v1/chats/{chat['id']}/messages/search",
                            headers=_h(bob), params={"q": "فردا"}).json()
    assert [m["text"] for m in found] == ["قرار فردا ساعت پنج"]

    empty = sync_client.get(f"/api/v1/chats/{chat['id']}/messages/search",
                            headers=_h(bob), params={"q": "چیزی که نیست"}).json()
    assert empty == []


def test_retry_with_the_same_client_id_does_not_duplicate(sync_client: TestClient):
    alice, bob = _register(sync_client), _register(sync_client)
    chat = _chat(sync_client, alice, bob)
    client_id = uuid.uuid4().hex

    first = _send(sync_client, alice, chat["id"], "یک‌بار", client_message_id=client_id)
    second = _send(sync_client, alice, chat["id"], "یک‌بار", client_message_id=client_id)
    assert first["id"] == second["id"]

    history = sync_client.get(f"/api/v1/chats/{chat['id']}/messages", headers=_h(bob)).json()
    assert len([m for m in history["items"] if m["id"] == first["id"]]) == 1
    assert len(history["items"]) == 1


def test_read_state_is_per_member(sync_client: TestClient):
    alice, bob = _register(sync_client), _register(sync_client)
    chat = _chat(sync_client, alice, bob)
    _send(sync_client, alice, chat["id"], "a")
    _send(sync_client, alice, chat["id"], "b")

    bob_row = next(r for r in sync_client.get("/api/v1/chats", headers=_h(bob)).json()
                   if r["id"] == chat["id"])
    assert bob_row["unread_count"] == 2
    sync_client.post(f"/api/v1/chats/{chat['id']}/read", headers=_h(bob),
                     params={"seq": bob_row["last_seq"]})
    bob_row = next(r for r in sync_client.get("/api/v1/chats", headers=_h(bob)).json()
                   if r["id"] == chat["id"])
    assert bob_row["unread_count"] == 0
    alice_row = next(r for r in sync_client.get("/api/v1/chats", headers=_h(alice)).json()
                     if r["id"] == chat["id"])
    assert alice_row["unread_count"] == 0


def test_http_read_receipt_broadcasts_read_updated(sync_client: TestClient):
    """The read button uses the REST endpoint; the sender must still be told."""
    alice, bob = _register(sync_client), _register(sync_client)
    chat = _chat(sync_client, alice, bob)
    _send(sync_client, alice, chat["id"], "خوانده شود")

    with sync_client.websocket_connect(f"/api/v1/ws?token={alice['access_token']}") as socket:
        assert socket.receive_json()["type"] == "ready"
        row = next(r for r in sync_client.get("/api/v1/chats", headers=_h(bob)).json()
                   if r["id"] == chat["id"])
        assert sync_client.post(f"/api/v1/chats/{chat['id']}/read", headers=_h(bob),
                                params={"seq": row["last_seq"]}).status_code == 204
        for _ in range(10):
            frame = socket.receive_json()
            if frame["type"] == "read.updated":
                assert frame["data"]["chat_id"] == chat["id"]
                assert frame["data"]["user_id"] != alice["user"]["id"] if "user" in alice else True
                break
        else:
            pytest.fail("no read.updated event for the sender")
