"""Groups, channels, roles and membership rules."""
from __future__ import annotations

from tests.conftest import auth, register_user


async def _ids(client, *users) -> list[str]:
    return [(await client.get("/api/v1/users/me", headers=auth(u["access_token"]))).json()["id"]
            for u in users]


async def test_group_creation_and_members(client, alice, bob):
    alice_id, bob_id = await _ids(client, alice, bob)
    created = await client.post(
        "/api/v1/chats", headers=auth(alice["access_token"]),
        json={"type": "group", "title": "Team", "member_ids": [bob_id]},
    )
    assert created.status_code == 201
    chat = created.json()
    assert chat["type"] == "group"
    assert chat["member_count"] == 2
    assert chat["my_role"] == "owner"

    members = (await client.get(f"/api/v1/chats/{chat['id']}/members",
                                headers=auth(bob["access_token"]))).json()
    assert {m["role"] for m in members} == {"owner", "member"}

    # The member sees the group in their chat list too.
    bob_chats = (await client.get("/api/v1/chats", headers=auth(bob["access_token"]))).json()
    assert chat["id"] in [c["id"] for c in bob_chats]
    assert alice_id != bob_id


async def test_member_management_permissions(client, alice, bob):
    _alice_id, bob_id = await _ids(client, alice, bob)
    carol = await register_user(client, "carol_test")
    carol_id = (await client.get("/api/v1/users/me", headers=auth(carol["access_token"]))).json()["id"]

    chat = (await client.post("/api/v1/chats", headers=auth(alice["access_token"]),
                              json={"type": "group", "title": "Perms",
                                    "member_ids": [bob_id]})).json()

    # A plain member cannot add people.
    denied = await client.post(f"/api/v1/chats/{chat['id']}/members",
                               headers=auth(bob["access_token"]),
                               json={"user_ids": [carol_id]})
    assert denied.status_code == 403

    added = await client.post(f"/api/v1/chats/{chat['id']}/members",
                              headers=auth(alice["access_token"]),
                              json={"user_ids": [carol_id]})
    assert added.status_code == 201
    assert added.json()[0]["user_id"] == carol_id

    # Owner promotes Bob; Bob may then add people.
    promoted = await client.patch(f"/api/v1/chats/{chat['id']}/members/{bob_id}",
                                  headers=auth(alice["access_token"]), json={"role": "admin"})
    assert promoted.status_code == 200
    assert promoted.json()["role"] == "admin"

    dave = await register_user(client, "dave_test")
    dave_id = (await client.get("/api/v1/users/me", headers=auth(dave["access_token"]))).json()["id"]
    added_by_admin = await client.post(f"/api/v1/chats/{chat['id']}/members",
                                       headers=auth(bob["access_token"]),
                                       json={"user_ids": [dave_id]})
    assert added_by_admin.status_code == 201

    chat_state = (await client.get(f"/api/v1/chats/{chat['id']}",
                                   headers=auth(alice["access_token"]))).json()
    assert chat_state["member_count"] == 4


async def test_admin_cannot_remove_peer_admin(client, alice, bob):
    _alice, bob_id = await _ids(client, alice, bob)
    chat = (await client.post("/api/v1/chats", headers=auth(alice["access_token"]),
                              json={"type": "group", "title": "Peers",
                                    "member_ids": [bob_id]})).json()
    await client.patch(f"/api/v1/chats/{chat['id']}/members/{bob_id}",
                       headers=auth(alice["access_token"]), json={"role": "admin"})

    carol = await register_user(client, "carol_two")
    carol_id = (await client.get("/api/v1/users/me", headers=auth(carol["access_token"]))).json()["id"]
    await client.post(f"/api/v1/chats/{chat['id']}/members", headers=auth(bob["access_token"]),
                      json={"user_ids": [carol_id], "role": "admin"})

    removed = await client.delete(f"/api/v1/chats/{chat['id']}/members/{carol_id}",
                                  headers=auth(bob["access_token"]))
    assert removed.status_code == 403


async def test_leave_group_and_count(client, alice, bob):
    _alice, bob_id = await _ids(client, alice, bob)
    chat = (await client.post("/api/v1/chats", headers=auth(alice["access_token"]),
                              json={"type": "group", "title": "Leavers",
                                    "member_ids": [bob_id]})).json()

    left = await client.post(f"/api/v1/chats/{chat['id']}/leave", headers=auth(bob["access_token"]))
    assert left.status_code == 204

    state = (await client.get(f"/api/v1/chats/{chat['id']}",
                              headers=auth(alice["access_token"]))).json()
    assert state["member_count"] == 1

    # Bob no longer sees or can post to the group.
    chats = (await client.get("/api/v1/chats", headers=auth(bob["access_token"]))).json()
    assert chat["id"] not in [c["id"] for c in chats]


async def test_owner_must_transfer_before_leaving(client, alice):
    chat = (await client.post("/api/v1/chats", headers=auth(alice["access_token"]),
                              json={"type": "group", "title": "Solo"})).json()
    response = await client.post(f"/api/v1/chats/{chat['id']}/leave",
                                 headers=auth(alice["access_token"]))
    assert response.status_code == 409


async def test_channel_only_admins_post(client, alice, bob):
    _alice, bob_id = await _ids(client, alice, bob)
    channel = (await client.post("/api/v1/chats", headers=auth(alice["access_token"]),
                                 json={"type": "channel", "title": "News",
                                       "member_ids": [bob_id]})).json()

    denied = await client.post(f"/api/v1/chats/{channel['id']}/messages",
                               headers=auth(bob["access_token"]),
                               json={"type": "text", "text": "subscriber post"})
    assert denied.status_code == 403

    allowed = await client.post(f"/api/v1/chats/{channel['id']}/messages",
                                headers=auth(alice["access_token"]),
                                json={"type": "text", "text": "announcement"})
    assert allowed.status_code == 201

    history = (await client.get(f"/api/v1/chats/{channel['id']}/messages",
                                headers=auth(bob["access_token"]))).json()
    assert len(history["items"]) == 1


async def test_group_requires_title_and_known_members(client, alice):
    missing_title = await client.post("/api/v1/chats", headers=auth(alice["access_token"]),
                                      json={"type": "group"})
    assert missing_title.status_code == 400

    import uuid

    unknown = await client.post("/api/v1/chats", headers=auth(alice["access_token"]),
                                json={"type": "group", "title": "Ghosts",
                                      "member_ids": [str(uuid.uuid4())]})
    assert unknown.status_code == 404


async def test_private_chat_cannot_be_deleted(client, alice, bob):
    bob_id = (await client.get("/api/v1/users/me", headers=auth(bob["access_token"]))).json()["id"]
    chat = (await client.post("/api/v1/chats", headers=auth(alice["access_token"]),
                              json={"type": "private", "member_ids": [bob_id]})).json()
    response = await client.delete(f"/api/v1/chats/{chat['id']}",
                                   headers=auth(alice["access_token"]))
    assert response.status_code == 400
