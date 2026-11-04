"""Phase 3 — dialog list contract used by the Android main screen.

Covers what the client's dialogs adapter needs: preview payload, pinning,
read counter, archive, folders, delete and duplicate-free realtime frames.
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
    username = f"dlg_{uuid.uuid4().hex[:8]}"
    challenge = client.post("/api/v1/auth/otp/request",
                            json={"username": username, "purpose": "register"})
    assert challenge.status_code == 202, challenge.text
    response = client.post("/api/v1/auth/register", json={
        "username": username, "password": "DialogsPass123", "display_name": username,
        "registration_token": challenge.json().get("registration_token"),
    })
    assert response.status_code == 201, response.text
    return response.json()


def _headers(tokens: dict) -> dict:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


def _pair(client: TestClient) -> tuple[dict, dict, dict]:
    alice, bob = _register(client), _register(client)
    bob_id = client.get("/api/v1/users/me", headers=_headers(bob)).json()["id"]
    chat = client.post("/api/v1/chats", headers=_headers(alice),
                       json={"type": "private", "member_ids": [bob_id]}).json()
    return alice, bob, chat


def _dialogs(client: TestClient, tokens: dict, **params) -> list[dict]:
    response = client.get("/api/v1/chats", headers=_headers(tokens), params=params)
    assert response.status_code == 200, response.text
    return response.json()


def test_dialog_row_carries_everything_the_row_renders(sync_client: TestClient):
    alice, _bob, chat = _pair(sync_client)
    sent = sync_client.post(f"/api/v1/chats/{chat['id']}/messages",
                            headers=_headers(alice), json={"text": "سلام از آلیس"})
    assert sent.status_code == 201, sent.text

    row = next(r for r in _dialogs(sync_client, alice) if r["id"] == chat["id"])
    # preview text + sender + time
    assert row["last_message"]["text"] == "سلام از آلیس"
    assert row["last_message"]["sender_id"] is not None
    assert row["last_message"]["created_at"]
    # counters, ordering keys, presence and avatar hook
    assert row["unread_count"] == 0
    assert row["pinned"] is False and row["archived"] is False
    assert row["last_seq"] >= 1 and row["peer"]["is_online"] in (True, False)
    assert "avatar_file_id" in row["peer"] and "avatar_file_id" in row


def test_pin_changes_order_and_payload(sync_client: TestClient):
    alice, _bob, chat = _pair(sync_client)
    peer = _register(sync_client)
    peer_id = sync_client.get("/api/v1/users/me", headers=_headers(peer)).json()["id"]
    second = sync_client.post("/api/v1/chats", headers=_headers(alice),
                              json={"type": "private", "member_ids": [peer_id]}).json()

    assert sync_client.post(f"/api/v1/chats/{second['id']}/pin",
                            headers=_headers(alice), json={"pinned": True}).status_code == 204
    rows = _dialogs(sync_client, alice)
    assert rows[0]["id"] == second["id"] and rows[0]["pinned"] is True

    assert sync_client.post(f"/api/v1/chats/{second['id']}/pin",
                            headers=_headers(alice), json={"pinned": False}).status_code == 204
    assert {r["id"]: r["pinned"] for r in _dialogs(sync_client, alice)}[second["id"]] is False
    assert chat["id"] in {r["id"] for r in _dialogs(sync_client, alice)}


def test_read_receipt_clears_the_unread_counter(sync_client: TestClient):
    alice, bob, chat = _pair(sync_client)
    body = {"text": "پیام تست"}
    assert sync_client.post(f"/api/v1/chats/{chat['id']}/messages",
                            headers=_headers(alice), json=body).status_code == 201
    assert sync_client.post(f"/api/v1/chats/{chat['id']}/messages",
                            headers=_headers(alice), json=body).status_code == 201

    unread = next(r for r in _dialogs(sync_client, bob) if r["id"] == chat["id"])["unread_count"]
    assert unread == 2

    assert sync_client.post(f"/api/v1/chats/{chat['id']}/read",
                            headers=_headers(bob), params={"seq": 2}).status_code == 204
    assert next(r for r in _dialogs(sync_client, bob) if r["id"] == chat["id"])["unread_count"] == 0


def test_archive_moves_the_dialog_between_tabs(sync_client: TestClient):
    alice, _bob, chat = _pair(sync_client)

    assert sync_client.post(f"/api/v1/chats/{chat['id']}/archive",
                            headers=_headers(alice), json={"archived": True}).status_code == 204
    assert chat["id"] not in {r["id"] for r in _dialogs(sync_client, alice)}
    archived = _dialogs(sync_client, alice, archived="true")
    assert chat["id"] in {r["id"] for r in archived}
    assert next(r for r in archived if r["id"] == chat["id"])["archived"] is True

    assert sync_client.post(f"/api/v1/chats/{chat['id']}/archive",
                            headers=_headers(alice), json={"archived": False}).status_code == 204
    assert chat["id"] in {r["id"] for r in _dialogs(sync_client, alice)}


def test_delete_follows_the_backend_policy(sync_client: TestClient):
    # Private chats are intentionally not deletable (history is shared and the
    # peer keeps their copy); the dialog menu offers archive for those instead.
    alice, _bob, chat = _pair(sync_client)
    refused = sync_client.delete(f"/api/v1/chats/{chat['id']}", headers=_headers(alice))
    assert refused.status_code == 400
    assert chat["id"] in {r["id"] for r in _dialogs(sync_client, alice)}

    # A group owned by the user can really be deleted and leaves the list.
    group = sync_client.post("/api/v1/chats", headers=_headers(alice),
                             json={"type": "group", "title": "تیم", "member_ids": []}).json()
    assert group["id"] in {r["id"] for r in _dialogs(sync_client, alice)}
    assert sync_client.delete(f"/api/v1/chats/{group['id']}",
                              headers=_headers(alice)).status_code == 204
    assert group["id"] not in {r["id"] for r in _dialogs(sync_client, alice)}


def test_folders_list_their_chat_ids(sync_client: TestClient):
    alice, _bob, chat = _pair(sync_client)
    folder = sync_client.post("/api/v1/folders", headers=_headers(alice),
                              json={"name": "کاری", "position": 0})
    assert folder.status_code == 201, folder.text
    folder_id = folder.json()["id"]
    assert sync_client.put(f"/api/v1/folders/{folder_id}/chats", headers=_headers(alice),
                           json={"chat_ids": [chat["id"]]}).status_code == 200
    listed = sync_client.get("/api/v1/folders", headers=_headers(alice)).json()
    assert listed[0]["chat_ids"] == [chat["id"]]
    # the folder's chats are a real subset the tab can render
    assert {r["id"] for r in _dialogs(sync_client, alice)} >= {chat["id"]}


def test_paging_overlaps_nothing_and_ends_cleanly(sync_client: TestClient):
    alice, _bob, _chat = _pair(sync_client)
    # Re-opening a private chat with the same peer must not duplicate it, so
    # build the extra rows from distinct peers.
    for _ in range(3):
        peer = _register(sync_client)
        peer_id = sync_client.get("/api/v1/users/me", headers=_headers(peer)).json()["id"]
        assert sync_client.post("/api/v1/chats", headers=_headers(alice),
                                json={"type": "private", "member_ids": [peer_id]}).status_code == 201

    first = _dialogs(sync_client, alice, limit=2, offset=0)
    second = _dialogs(sync_client, alice, limit=2, offset=2)
    assert len(first) == 2
    assert not ({r["id"] for r in first} & {r["id"] for r in second})
    assert len({r["id"] for r in first + second}) == len(first + second)


def test_realtime_frames_are_identifiable_and_unique(sync_client: TestClient):
    alice, bob, chat = _pair(sync_client)
    bob_open = sync_client.websocket_connect(f"/api/v1/ws?token={bob['access_token']}")
    with bob_open as socket:
        assert socket.receive_json()["type"] == "ready"
        assert sync_client.post(f"/api/v1/chats/{chat['id']}/messages",
                                headers=_headers(alice), json={"text": "یک"}).status_code == 201
        assert sync_client.post(f"/api/v1/chats/{chat['id']}/pin",
                                headers=_headers(alice), json={"pinned": True}).status_code == 204

        seen = []
        for _ in range(12):
            try:
                frame = socket.receive_json()
            except Exception:
                break
            seen.append(frame)
            if len([f for f in seen if f["type"] == "dialog.updated"]) >= 1 and \
                    len([f for f in seen if f["type"] == "message.new"]) >= 1:
                break

        types = [f["type"] for f in seen]
        assert "message.new" in types, types
        assert "dialog.updated" in types, types
        ids = [f.get("event_id") for f in seen]
        assert all(ids), "every frame needs an event_id for de-duplication"
        assert len(set(ids)) == len(ids)


def test_archived_dialogs_do_not_leak_into_the_main_list(sync_client: TestClient):
    alice, _bob, chat = _pair(sync_client)
    peer = _register(sync_client)
    peer_id = sync_client.get("/api/v1/users/me", headers=_headers(peer)).json()["id"]
    other = sync_client.post("/api/v1/chats", headers=_headers(alice),
                             json={"type": "private", "member_ids": [peer_id]}).json()
    sync_client.post(f"/api/v1/chats/{chat['id']}/archive",
                     headers=_headers(alice), json={"archived": True})

    main = {r["id"] for r in _dialogs(sync_client, alice)}
    archived = {r["id"] for r in _dialogs(sync_client, alice, archived="true")}
    assert chat["id"] not in main and other["id"] in main
    assert archived == {chat["id"]}
