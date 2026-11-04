"""Profiles, discovery, contacts and privacy rules."""
from __future__ import annotations

from tests.conftest import auth


async def test_profile_update_and_read_back(client, alice):
    response = await client.patch(
        "/api/v1/users/me",
        headers=auth(alice["access_token"]),
        json={"display_name": "Alice Cooper", "bio": "hello", "settings": {"theme": "dark"}},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["display_name"] == "Alice Cooper"
    assert body["settings"] == {"theme": "dark"}


async def test_search_excludes_self_and_finds_others(client, alice, bob):
    response = await client.get(
        "/api/v1/users/search", headers=auth(alice["access_token"]), params={"q": "bob"}
    )
    assert response.status_code == 200
    usernames = [u["username"] for u in response.json()]
    assert "bob_test" in usernames
    assert "alice_test" not in usernames


async def test_contact_lifecycle(client, alice, bob):
    me = (await client.get("/api/v1/users/me", headers=auth(alice["access_token"]))).json()
    bob_me = (await client.get("/api/v1/users/me", headers=auth(bob["access_token"]))).json()

    added = await client.post(
        "/api/v1/contacts",
        headers=auth(alice["access_token"]),
        json={"contact_id": bob_me["id"], "first_name": "Bob"},
    )
    assert added.status_code == 201
    assert added.json()["username"] == "bob_test"

    listed = await client.get("/api/v1/contacts", headers=auth(alice["access_token"]))
    assert [c["contact_id"] for c in listed.json()] == [bob_me["id"]]

    # Adding the same contact twice must not duplicate it.
    await client.post("/api/v1/contacts", headers=auth(alice["access_token"]),
                      json={"contact_id": bob_me["id"]})
    listed_again = await client.get("/api/v1/contacts", headers=auth(alice["access_token"]))
    assert len(listed_again.json()) == 1

    removed = await client.delete(f"/api/v1/contacts/{bob_me['id']}",
                                  headers=auth(alice["access_token"]))
    assert removed.status_code == 204
    assert (await client.get("/api/v1/contacts", headers=auth(alice["access_token"]))).json() == []
    assert me["id"] != bob_me["id"]


async def test_blocking_hides_presence(client, alice, bob):
    bob_me = (await client.get("/api/v1/users/me", headers=auth(bob["access_token"]))).json()
    before = await client.get(f"/api/v1/users/{bob_me['id']}", headers=auth(alice["access_token"]))
    assert before.status_code == 200

    await client.post(f"/api/v1/users/{bob_me['id']}/block", headers=auth(alice["access_token"]))
    blocked_list = await client.get("/api/v1/users/blocked", headers=auth(alice["access_token"]))
    assert [u["id"] for u in blocked_list.json()] == [bob_me["id"]]

    # Bob is blocked by Alice: his presence is no longer exposed to her.
    after = await client.get(f"/api/v1/users/{bob_me['id']}", headers=auth(alice["access_token"]))
    assert after.json()["last_seen_at"] is None

    await client.delete(f"/api/v1/users/{bob_me['id']}/block", headers=auth(alice["access_token"]))
    assert (await client.get("/api/v1/users/blocked",
                             headers=auth(alice["access_token"]))).json() == []


async def test_cannot_block_self(client, alice):
    me = (await client.get("/api/v1/users/me", headers=auth(alice["access_token"]))).json()
    response = await client.post(f"/api/v1/users/{me['id']}/block",
                                 headers=auth(alice["access_token"]))
    assert response.status_code == 400
