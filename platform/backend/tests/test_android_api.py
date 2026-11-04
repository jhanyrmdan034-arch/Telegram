"""API surface required by the Android client (phases 2-4 of the integration).

Every case here maps to a screen behaviour in the existing UI:
OTP sign-in, dialog list (last message / unread / pinned / peer presence),
chat folders, forwarding, in-chat search and the public bootstrap config.
"""
from __future__ import annotations

import uuid

from tests.conftest import auth, register_user


async def _user_id(client, user) -> str:
    return (await client.get("/api/v1/users/me", headers=auth(user["access_token"]))).json()["id"]


async def _private_chat(client, alice, bob) -> str:
    bob_id = await _user_id(client, bob)
    response = await client.post("/api/v1/chats", headers=auth(alice["access_token"]),
                                 json={"type": "private", "member_ids": [bob_id]})
    assert response.status_code == 201
    return response.json()["id"]


# --------------------------------------------------------------------------- #
# Phase 2 — OTP sign-in
# --------------------------------------------------------------------------- #
async def test_otp_login_flow(client, alice):
    challenge = await client.post("/api/v1/auth/otp/request",
                                  json={"username": alice["username"], "purpose": "login"})
    assert challenge.status_code == 202
    body = challenge.json()
    assert body["delivery"] == "log"
    assert body["expires_in"] == 300
    code = body["debug_code"]
    assert code and len(code) == 6

    verified = await client.post("/api/v1/auth/otp/verify", json={
        "username": alice["username"], "code": code, "device_name": "Pixel", "platform": "android",
    })
    assert verified.status_code == 200
    tokens = verified.json()
    assert (await client.get("/api/v1/users/me", headers=auth(tokens["access_token"]))).json()[
        "username"] == alice["username"]


async def test_otp_is_single_use(client, alice):
    code = (await client.post("/api/v1/auth/otp/request",
                              json={"username": alice["username"]})).json()["debug_code"]
    first = await client.post("/api/v1/auth/otp/verify",
                              json={"username": alice["username"], "code": code})
    assert first.status_code == 200
    replay = await client.post("/api/v1/auth/otp/verify",
                               json={"username": alice["username"], "code": code})
    assert replay.status_code == 401


async def test_new_otp_request_invalidates_previous_code(client, alice):
    first = (await client.post("/api/v1/auth/otp/request",
                               json={"username": alice["username"]})).json()["debug_code"]
    second = (await client.post("/api/v1/auth/otp/request",
                                json={"username": alice["username"]})).json()["debug_code"]

    stale = await client.post("/api/v1/auth/otp/verify",
                              json={"username": alice["username"], "code": first})
    if first != second:
        assert stale.status_code == 401
    fresh = await client.post("/api/v1/auth/otp/verify",
                              json={"username": alice["username"], "code": second})
    assert fresh.status_code == 200


async def test_otp_wrong_code_counts_attempts_then_blocks(client, alice):
    challenge = await client.post("/api/v1/auth/otp/request",
                                  json={"username": alice["username"]})
    assert challenge.status_code == 202
    assert challenge.json()["debug_code"]

    wrong = "000000" if challenge.json()["debug_code"] != "000000" else "111111"
    for _ in range(5):
        response = await client.post("/api/v1/auth/otp/verify",
                                     json={"username": alice["username"], "code": wrong})
        assert response.status_code == 401

    blocked = await client.post("/api/v1/auth/otp/verify",
                                json={"username": alice["username"],
                                      "code": challenge.json()["debug_code"]})
    assert blocked.status_code == 429


async def test_otp_for_unknown_account_reports_not_found(client):
    response = await client.post("/api/v1/auth/otp/request",
                                 json={"username": "ghost_user", "purpose": "login"})
    assert response.status_code == 404


async def test_otp_rejected_for_blocked_account(client, staff_user, alice):
    user_id = await _user_id(client, alice)
    await client.post(f"/api/v1/admin/users/{user_id}/block",
                      headers=auth(staff_user["access_token"]), json={"blocked": True})
    response = await client.post("/api/v1/auth/otp/request",
                                 json={"username": alice["username"]})
    assert response.status_code == 403


# --------------------------------------------------------------------------- #
# Phase 3 — dialog list rendering data
# --------------------------------------------------------------------------- #
async def test_chat_list_carries_last_message_and_unread(client, alice, bob):
    chat_id = await _private_chat(client, alice, bob)
    for i in range(3):
        await client.post(f"/api/v1/chats/{chat_id}/messages",
                          headers=auth(alice["access_token"]),
                          json={"type": "text", "text": f"msg {i}"})

    listing = (await client.get("/api/v1/chats", headers=auth(bob["access_token"]))).json()
    chat = next(c for c in listing if c["id"] == chat_id)
    assert chat["last_message"]["text"] == "msg 2"
    assert chat["unread_count"] == 3
    assert chat["peer"]["username"] == "alice_test"
    assert chat["peer"]["is_online"] is False  # no websocket connected in this test

    await client.post(f"/api/v1/chats/{chat_id}/read", headers=auth(bob["access_token"]),
                      params={"seq": chat["last_seq"]})
    refreshed = (await client.get(f"/api/v1/chats/{chat_id}",
                                  headers=auth(bob["access_token"]))).json()
    assert refreshed["unread_count"] == 0


async def test_pinned_chats_sort_first(client, alice, bob):
    first = await _private_chat(client, alice, bob)
    carol = await register_user(client, "carol_pin")
    second = (await client.post("/api/v1/chats", headers=auth(alice["access_token"]),
                                json={"type": "private",
                                      "member_ids": [await _user_id(client, carol)]})).json()["id"]

    pin = await client.post(f"/api/v1/chats/{first}/pin", headers=auth(alice["access_token"]),
                            json={"pinned": True})
    assert pin.status_code == 204

    listing = (await client.get("/api/v1/chats", headers=auth(alice["access_token"]))).json()
    assert listing[0]["id"] == first
    assert listing[0]["pinned"] is True
    assert all(c["id"] != second or c["pinned"] is False for c in listing)

    pinned_only = (await client.get("/api/v1/chats/pinned",
                                    headers=auth(alice["access_token"]))).json()
    assert [c["id"] for c in pinned_only] == [first]

    await client.post(f"/api/v1/chats/{first}/pin", headers=auth(alice["access_token"]),
                      json={"pinned": False})
    assert (await client.get("/api/v1/chats/pinned",
                             headers=auth(alice["access_token"]))).json() == []


# --------------------------------------------------------------------------- #
# Phase 3 — chat folders
# --------------------------------------------------------------------------- #
async def test_folder_crud_and_membership(client, alice, bob):
    chat_id = await _private_chat(client, alice, bob)

    created = await client.post("/api/v1/folders", headers=auth(alice["access_token"]),
                                json={"name": "Work", "position": 1})
    assert created.status_code == 201
    folder_id = created.json()["id"]

    duplicate = await client.post("/api/v1/folders", headers=auth(alice["access_token"]),
                                  json={"name": "Work"})
    assert duplicate.status_code == 409

    assigned = await client.put(f"/api/v1/folders/{folder_id}/chats",
                                headers=auth(alice["access_token"]),
                                json={"chat_ids": [chat_id]})
    assert assigned.status_code == 200
    assert assigned.json()["chat_ids"] == [chat_id]

    chats = (await client.get(f"/api/v1/folders/{folder_id}/chats",
                              headers=auth(alice["access_token"]))).json()
    assert [c["id"] for c in chats] == [chat_id]

    # Bob is a member of the chat but cannot put it in *his* folder without adding it.
    bob_folder = (await client.post("/api/v1/folders", headers=auth(bob["access_token"]),
                                    json={"name": "Friends"})).json()
    assert bob_folder["chat_ids"] == []

    # A chat the user is not in must be rejected.
    other_chat = (await client.post("/api/v1/chats", headers=auth(bob["access_token"]),
                                    json={"type": "saved"})).json()
    denied = await client.put(f"/api/v1/folders/{folder_id}/chats",
                              headers=auth(alice["access_token"]),
                              json={"chat_ids": [other_chat["id"]]})
    assert denied.status_code == 404

    renamed = await client.patch(f"/api/v1/folders/{folder_id}",
                                 headers=auth(alice["access_token"]),
                                 json={"name": "Projects", "position": 2})
    assert renamed.json()["name"] == "Projects"

    # Folder ownership is enforced.
    foreign = await client.patch(f"/api/v1/folders/{folder_id}",
                                 headers=auth(bob["access_token"]), json={"name": "Hijack"})
    assert foreign.status_code == 404

    deleted = await client.delete(f"/api/v1/folders/{folder_id}",
                                  headers=auth(alice["access_token"]))
    assert deleted.status_code == 204
    assert (await client.get("/api/v1/folders", headers=auth(alice["access_token"]))).json() == []


# --------------------------------------------------------------------------- #
# Phase 4 — messaging behaviours
# --------------------------------------------------------------------------- #
async def test_forward_message_copies_to_target_chat(client, alice, bob):
    source_chat = await _private_chat(client, alice, bob)
    message = (await client.post(f"/api/v1/chats/{source_chat}/messages",
                                 headers=auth(alice["access_token"]),
                                 json={"type": "text", "text": "forward me"})).json()

    carol = await register_user(client, "carol_fwd")
    target = (await client.post("/api/v1/chats", headers=auth(alice["access_token"]),
                                json={"type": "private",
                                      "member_ids": [await _user_id(client, carol)]})).json()

    forwarded = await client.post(f"/api/v1/messages/{message['id']}/forward",
                                  headers=auth(alice["access_token"]),
                                  json={"chat_ids": [target["id"]]})
    assert forwarded.status_code == 201
    copy = forwarded.json()[0]
    assert copy["chat_id"] == target["id"]
    assert copy["text"] == "forward me"
    assert copy["meta"]["forwarded_from"]["message_id"] == message["id"]

    history = (await client.get(f"/api/v1/chats/{target['id']}/messages",
                                headers=auth(carol["access_token"]))).json()
    assert len(history["items"]) == 1


async def test_forward_requires_membership_of_source_and_target(client, alice, bob):
    chat_id = await _private_chat(client, alice, bob)
    message = (await client.post(f"/api/v1/chats/{chat_id}/messages",
                                 headers=auth(alice["access_token"]),
                                 json={"type": "text", "text": "private"})).json()

    eve = await register_user(client, "eve_fwd")
    denied = await client.post(f"/api/v1/messages/{message['id']}/forward",
                               headers=auth(eve["access_token"]),
                               json={"chat_ids": [chat_id]})
    assert denied.status_code == 403


async def test_in_chat_search(client, alice, bob):
    chat_id = await _private_chat(client, alice, bob)
    for text in ("deployment failed", "deployment succeeded", "lunch plans"):
        await client.post(f"/api/v1/chats/{chat_id}/messages",
                          headers=auth(alice["access_token"]),
                          json={"type": "text", "text": text})

    found = (await client.get(f"/api/v1/chats/{chat_id}/messages/search",
                              headers=auth(bob["access_token"]),
                              params={"q": "deployment"})).json()
    assert len(found) == 2
    assert (await client.get(f"/api/v1/chats/{chat_id}/messages/search",
                             headers=auth(bob["access_token"]),
                             params={"q": "nonexistent"})).json() == []

    eve = await register_user(client, "eve_search")
    assert (await client.get(f"/api/v1/chats/{chat_id}/messages/search",
                             headers=auth(eve["access_token"]),
                             params={"q": "deployment"})).status_code == 403


async def test_message_idempotency_key_is_preserved(client, alice, bob):
    """The client retries failed sends; it needs the idempotency key echoed back."""
    chat_id = await _private_chat(client, alice, bob)
    sent = (await client.post(f"/api/v1/chats/{chat_id}/messages",
                              headers=auth(alice["access_token"]),
                              json={"type": "text", "text": "retry me",
                                    "client_message_id": "local-42"})).json()
    assert sent["meta"]["client_message_id"] == "local-42"


# --------------------------------------------------------------------------- #
# Public bootstrap config
# --------------------------------------------------------------------------- #
async def test_public_config_requires_no_auth(client, staff_user):
    response = await client.get("/api/v1/config")
    assert response.status_code == 200
    body = response.json()
    assert body["registration_enabled"] is True
    assert body["calls_enabled"] is True
    assert body["max_file_size_bytes"] > 0
    assert body["maintenance"] is False

    await client.post("/api/v1/admin/maintenance", headers=auth(staff_user["access_token"]),
                      json={"enabled": True, "message": "Deploying"})
    assert (await client.get("/api/v1/config")).json()["maintenance"] is True

    await client.post("/api/v1/admin/maintenance", headers=auth(staff_user["access_token"]),
                      json={"enabled": False})


async def test_unknown_chat_ids_are_rejected(client, alice):
    assert (await client.get(f"/api/v1/chats/{uuid.uuid4()}",
                             headers=auth(alice["access_token"]))).status_code == 404
