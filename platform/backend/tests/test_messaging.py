"""Private chats, message ordering, pagination and access control."""
from __future__ import annotations

import pytest

from tests.conftest import auth


async def _private_chat(client, alice, bob) -> str:
    bob_id = (await client.get("/api/v1/users/me", headers=auth(bob["access_token"]))).json()["id"]
    response = await client.post(
        "/api/v1/chats",
        headers=auth(alice["access_token"]),
        json={"type": "private", "member_ids": [bob_id]},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def test_private_chat_is_idempotent(client, alice, bob):
    first = await _private_chat(client, alice, bob)
    bob_id = (await client.get("/api/v1/users/me", headers=auth(bob["access_token"]))).json()["id"]
    second = await client.post(
        "/api/v1/chats", headers=auth(bob["access_token"]),
        json={"type": "private", "member_ids": [
            (await client.get("/api/v1/users/me", headers=auth(alice["access_token"]))).json()["id"]
        ]},
    )
    assert second.status_code == 201
    assert second.json()["id"] == first
    assert bob_id


async def test_send_and_receive_messages(client, alice, bob):
    chat_id = await _private_chat(client, alice, bob)

    sent = await client.post(
        f"/api/v1/chats/{chat_id}/messages", headers=auth(alice["access_token"]),
        json={"type": "text", "text": "hello bob"},
    )
    assert sent.status_code == 201
    message = sent.json()
    assert message["seq"] == 1
    assert message["text"] == "hello bob"

    reply = await client.post(
        f"/api/v1/chats/{chat_id}/messages", headers=auth(bob["access_token"]),
        json={"type": "text", "text": "hi alice", "reply_to_id": message["id"]},
    )
    assert reply.status_code == 201
    assert reply.json()["seq"] == 2
    assert reply.json()["reply_to_id"] == message["id"]

    history = await client.get(f"/api/v1/chats/{chat_id}/messages",
                               headers=auth(alice["access_token"]))
    assert [m["seq"] for m in history.json()["items"]] == [1, 2]


async def test_pagination(client, alice, bob):
    chat_id = await _private_chat(client, alice, bob)
    for i in range(5):
        await client.post(f"/api/v1/chats/{chat_id}/messages", headers=auth(alice["access_token"]),
                          json={"type": "text", "text": f"m{i}"})

    page1 = (await client.get(f"/api/v1/chats/{chat_id}/messages",
                              headers=auth(alice["access_token"]),
                              params={"limit": 2})).json()
    assert [m["seq"] for m in page1["items"]] == [4, 5]
    assert page1["next_before_seq"] == 4

    page2 = (await client.get(f"/api/v1/chats/{chat_id}/messages",
                              headers=auth(alice["access_token"]),
                              params={"limit": 2, "before_seq": page1["next_before_seq"]})).json()
    assert [m["seq"] for m in page2["items"]] == [2, 3]


async def test_edit_and_delete_permissions(client, alice, bob):
    chat_id = await _private_chat(client, alice, bob)
    message = (await client.post(f"/api/v1/chats/{chat_id}/messages",
                                 headers=auth(alice["access_token"]),
                                 json={"type": "text", "text": "original"})).json()

    forbidden = await client.patch(f"/api/v1/messages/{message['id']}",
                                   headers=auth(bob["access_token"]), json={"text": "hacked"})
    assert forbidden.status_code == 403

    edited = await client.patch(f"/api/v1/messages/{message['id']}",
                                headers=auth(alice["access_token"]), json={"text": "updated"})
    assert edited.status_code == 200
    assert edited.json()["text"] == "updated"
    assert edited.json()["edited_at"] is not None

    deleted = await client.delete(f"/api/v1/messages/{message['id']}",
                                  headers=auth(alice["access_token"]))
    assert deleted.status_code == 204
    history = (await client.get(f"/api/v1/chats/{chat_id}/messages",
                                headers=auth(bob["access_token"]))).json()
    assert history["items"] == []


async def test_empty_text_rejected(client, alice, bob):
    chat_id = await _private_chat(client, alice, bob)
    response = await client.post(f"/api/v1/chats/{chat_id}/messages",
                                 headers=auth(alice["access_token"]),
                                 json={"type": "text", "text": "   "})
    assert response.status_code == 422


async def test_non_member_cannot_read_or_write(client, alice, bob):
    chat_id = await _private_chat(client, alice, bob)
    from tests.conftest import register_user

    eve = await register_user(client, "eve_test")
    read = await client.get(f"/api/v1/chats/{chat_id}/messages", headers=auth(eve["access_token"]))
    assert read.status_code == 403
    write = await client.post(f"/api/v1/chats/{chat_id}/messages",
                              headers=auth(eve["access_token"]),
                              json={"type": "text", "text": "intruder"})
    assert write.status_code == 403


async def test_read_receipts_and_typing(client, alice, bob):
    chat_id = await _private_chat(client, alice, bob)
    message = (await client.post(f"/api/v1/chats/{chat_id}/messages",
                                 headers=auth(alice["access_token"]),
                                 json={"type": "text", "text": "read me"})).json()

    mark = await client.post(f"/api/v1/chats/{chat_id}/read",
                             headers=auth(bob["access_token"]), params={"seq": message["seq"]})
    assert mark.status_code == 204
    chat = (await client.get(f"/api/v1/chats/{chat_id}", headers=auth(bob["access_token"]))).json()
    assert chat["last_read_seq"] == message["seq"]

    typing = await client.post(f"/api/v1/chats/{chat_id}/typing",
                               headers=auth(alice["access_token"]))
    assert typing.status_code == 204


async def test_saved_messages_chat(client, alice):
    response = await client.post("/api/v1/chats", headers=auth(alice["access_token"]),
                                 json={"type": "saved"})
    assert response.status_code == 201
    chat = response.json()
    assert chat["type"] == "saved"
    again = await client.post("/api/v1/chats", headers=auth(alice["access_token"]),
                              json={"type": "saved"})
    assert again.json()["id"] == chat["id"]

    sent = await client.post(f"/api/v1/chats/{chat['id']}/messages",
                             headers=auth(alice["access_token"]),
                             json={"type": "text", "text": "note to self"})
    assert sent.status_code == 201


@pytest.mark.parametrize("payload", [
    {"type": "photo"},  # media requires a file
    {"type": "text"},
])
async def test_invalid_message_payloads(client, alice, bob, payload):
    chat_id = await _private_chat(client, alice, bob)
    response = await client.post(f"/api/v1/chats/{chat_id}/messages",
                                 headers=auth(alice["access_token"]), json=payload)
    assert response.status_code == 422
