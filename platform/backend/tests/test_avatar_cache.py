"""Avatar caching: a picture change must never be served from the old cache.

The Android row caches avatars by file id on disk and revalidates with the ETag
on the content endpoint, so both halves of that contract are asserted here.
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
    username = f"ava_{uuid.uuid4().hex[:8]}"
    challenge = client.post("/api/v1/auth/otp/request",
                            json={"username": username, "purpose": "register"})
    assert challenge.status_code == 202, challenge.text
    response = client.post("/api/v1/auth/register", json={
        "username": username, "password": "AvatarPass123", "display_name": username,
        "registration_token": challenge.json().get("registration_token"),
    })
    assert response.status_code == 201, response.text
    return response.json()


def _headers(tokens: dict) -> dict:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


def _upload_avatar(client: TestClient, tokens: dict, marker: bytes) -> str:
    response = client.post("/api/v1/files", headers=_headers(tokens),
                           files={"file": ("avatar.jpg", marker, "image/jpeg")},
                           data={"kind": "avatar"})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_new_avatar_gets_a_new_id_and_a_new_etag(sync_client: TestClient):
    tokens = _register(sync_client)

    first = _upload_avatar(sync_client, tokens, b"\xff\xd8first-picture-body")
    patched = sync_client.patch("/api/v1/users/me", headers=_headers(tokens),
                                json={"avatar_file_id": first})
    assert patched.status_code == 200, patched.text
    assert patched.json()["avatar_file_id"] == first

    first_get = sync_client.get(f"/api/v1/files/{first}/content", headers=_headers(tokens))
    assert first_get.status_code == 200
    first_etag = first_get.headers.get("ETag")
    assert first_etag, "content must carry an ETag for client-side revalidation"

    # Same file, unchanged: the client may keep its cached copy.
    again = sync_client.get(f"/api/v1/files/{first}/content",
                            headers={**_headers(tokens), "If-None-Match": first_etag})
    assert again.status_code == 304, again.text

    second = _upload_avatar(sync_client, tokens, b"\xff\xd8second-picture-body")
    patched = sync_client.patch("/api/v1/users/me", headers=_headers(tokens),
                                json={"avatar_file_id": second})
    assert patched.json()["avatar_file_id"] == second
    assert second != first, "a new upload must produce a new file id"

    second_get = sync_client.get(f"/api/v1/files/{second}/content", headers=_headers(tokens))
    assert second_get.status_code == 200
    assert second_get.headers["ETag"] != first_etag
    assert second_get.content != first_get.content
