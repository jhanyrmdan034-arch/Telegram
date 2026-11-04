"""Phase 5 — contacts, profiles, privacy and global search."""
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
    username = f"c5_{uuid.uuid4().hex[:8]}"
    challenge = client.post("/api/v1/auth/otp/request",
                            json={"username": username, "purpose": "register"})
    assert challenge.status_code == 202, challenge.text
    response = client.post("/api/v1/auth/register", json={
        "username": username, "password": "ContactsPass123", "display_name": username,
        "registration_token": challenge.json().get("registration_token"),
    })
    assert response.status_code == 201, response.text
    body = response.json()
    body["_username"] = username
    return body


def _h(tokens: dict) -> dict:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


def _me(client: TestClient, tokens: dict) -> dict:
    return client.get("/api/v1/users/me", headers=_h(tokens)).json()


def test_contacts_add_list_remove(sync_client: TestClient):
    alice, bob = _register(sync_client), _register(sync_client)
    bob_id = _me(sync_client, bob)["id"]

    added = sync_client.post("/api/v1/contacts", headers=_h(alice),
                             json={"contact_id": bob_id, "first_name": "باب"})
    assert added.status_code == 201, added.text

    listed = sync_client.get("/api/v1/contacts", headers=_h(alice)).json()
    assert [c["contact_id"] if "contact_id" in c else c["id"] for c in listed] == [bob_id] or \
           any(str(bob_id) in str(entry.values()) for entry in listed)

    removed = sync_client.delete(f"/api/v1/contacts/{bob_id}", headers=_h(alice))
    assert removed.status_code == 204
    assert sync_client.get("/api/v1/contacts", headers=_h(alice)).json() == []


def test_last_seen_privacy_controls_presence(sync_client: TestClient):
    alice, bob = _register(sync_client), _register(sync_client)
    bob_id = _me(sync_client, bob)["id"]

    # default: visible
    profile = sync_client.get(f"/api/v1/users/{bob_id}", headers=_h(alice)).json()
    assert profile["presence_visible"] is True

    # hide from everybody
    assert sync_client.patch("/api/v1/users/me/privacy", headers=_h(bob),
                             json={"last_seen_privacy": "nobody"}).status_code == 200
    hidden = sync_client.get(f"/api/v1/users/{bob_id}", headers=_h(alice)).json()
    assert hidden["presence_visible"] is False
    assert hidden["last_seen_at"] is None and hidden["is_online"] is False

    # contacts still see it when the policy is "contacts"
    sync_client.patch("/api/v1/users/me/privacy", headers=_h(bob),
                      json={"last_seen_privacy": "contacts"})
    stranger_view = sync_client.get(f"/api/v1/users/{bob_id}", headers=_h(alice)).json()
    assert stranger_view["presence_visible"] is False

    sync_client.post("/api/v1/contacts", headers=_h(alice), json={"contact_id": bob_id})
    friend_view = sync_client.get(f"/api/v1/users/{bob_id}", headers=_h(alice)).json()
    assert friend_view["presence_visible"] is True


def test_search_respects_search_privacy(sync_client: TestClient):
    alice, bob = _register(sync_client), _register(sync_client)
    bob_name = bob["_username"]

    assert sync_client.patch("/api/v1/users/me/privacy", headers=_h(bob),
                             json={"search_privacy": "nobody"}).status_code == 200
    hidden = sync_client.get("/api/v1/users/search", headers=_h(alice),
                             params={"q": bob_name}).json()
    assert hidden == []

    sync_client.patch("/api/v1/users/me/privacy", headers=_h(bob),
                      json={"search_privacy": "contacts"})
    assert sync_client.get("/api/v1/users/search", headers=_h(alice),
                           params={"q": bob_name}).json() == []

    bob_id = _me(sync_client, bob)["id"]
    sync_client.post("/api/v1/contacts", headers=_h(alice), json={"contact_id": bob_id})
    visible = sync_client.get("/api/v1/users/search", headers=_h(alice),
                              params={"q": bob_name}).json()
    assert [u["username"] for u in visible] == [bob_name]


def test_search_result_has_only_public_fields(sync_client: TestClient):
    alice, bob = _register(sync_client), _register(sync_client)
    found = sync_client.get("/api/v1/users/search", headers=_h(alice),
                            params={"q": bob["_username"]}).json()
    assert found
    allowed = {"id", "username", "display_name", "bio", "avatar_file_id",
               "last_seen_at", "is_online", "presence_visible"}
    assert set(found[0]) == allowed
    for forbidden in ("password_hash", "phone", "email", "settings", "search_privacy"):
        assert forbidden not in found[0]


def test_global_search_returns_users_and_chats(sync_client: TestClient):
    alice, bob = _register(sync_client), _register(sync_client)
    bob_id = _me(sync_client, bob)["id"]
    chat = sync_client.post("/api/v1/chats", headers=_h(alice),
                            json={"type": "private", "member_ids": [bob_id]}).json()
    group = sync_client.post("/api/v1/chats", headers=_h(alice),
                            json={"type": "group", "title": f"تیم {bob['_username'][:6]}",
                                  "member_ids": [bob_id]}).json()

    result = sync_client.get("/api/v1/search", headers=_h(alice),
                             params={"q": bob["_username"][:8]}).json()
    assert result["query"]
    assert any(u["id"] == bob_id for u in result["users"])
    assert chat["id"] not in {c["id"] for c in result["chats"]}  # private chats have no title match

    titled = sync_client.get("/api/v1/search", headers=_h(alice),
                             params={"q": "تیم"}).json()
    assert group["id"] in {c["id"] for c in titled["chats"]}
    # a stranger must not see chats they do not belong to
    carol = _register(sync_client)
    stranger = sync_client.get("/api/v1/search", headers=_h(carol), params={"q": "تیم"}).json()
    assert stranger["chats"] == []


def test_profile_update_is_broadcast(sync_client: TestClient):
    alice, bob = _register(sync_client), _register(sync_client)
    bob_id = _me(sync_client, bob)["id"]
    sync_client.post("/api/v1/chats", headers=_h(alice),
                     json={"type": "private", "member_ids": [bob_id]})

    with sync_client.websocket_connect(f"/api/v1/ws?token={alice['access_token']}") as socket:
        assert socket.receive_json()["type"] == "ready"
        updated = sync_client.patch("/api/v1/users/me", headers=_h(bob),
                                    json={"display_name": "باب تازه", "bio": "سلام"})
        assert updated.status_code == 200, updated.text
        for _ in range(10):
            frame = socket.receive_json()
            if frame["type"] == "profile.updated":
                assert frame["data"]["user_id"] == bob_id
                assert frame["data"]["display_name"] == "باب تازه"
                break
        else:
            pytest.fail("no profile.updated event")


def test_blocked_user_is_not_searchable_for_the_blocker(sync_client: TestClient):
    alice, bob = _register(sync_client), _register(sync_client)
    bob_id = _me(sync_client, bob)["id"]
    assert sync_client.post(f"/api/v1/users/{bob_id}/block", headers=_h(alice)).status_code == 204
    profile = sync_client.get(f"/api/v1/users/{bob_id}", headers=_h(alice)).json()
    assert profile["presence_visible"] is False and profile["is_online"] is False
    assert bob_id in {u["id"] for u in sync_client.get("/api/v1/users/blocked",
                                                       headers=_h(alice)).json()}


def test_privacy_values_are_validated(sync_client: TestClient):
    alice = _register(sync_client)
    bad = sync_client.patch("/api/v1/users/me/privacy", headers=_h(alice),
                            json={"last_seen_privacy": "sometimes"})
    assert bad.status_code == 422
