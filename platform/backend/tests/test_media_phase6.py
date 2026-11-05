"""Phase 6 — media upload, validation, access control, multipart and events."""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import create_app

JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01" + b"\x00" * 100
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
ELF = b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 100


@pytest.fixture
def sync_client(settings):
    application = create_app(settings=settings)
    with TestClient(application) as client:
        yield client


def _register(client: TestClient) -> dict:
    username = f"m6_{uuid.uuid4().hex[:8]}"
    challenge = client.post("/api/v1/auth/otp/request",
                            json={"username": username, "purpose": "register"})
    assert challenge.status_code == 202, challenge.text
    response = client.post("/api/v1/auth/register", json={
        "username": username, "password": "MediaPass1234", "display_name": username,
        "registration_token": challenge.json().get("registration_token"),
    })
    assert response.status_code == 201, response.text
    return response.json()


def _h(tokens: dict) -> dict:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


def _upload(client, tokens, body: bytes, kind: str = "photo", name: str = "a.jpg",
            mime: str = "image/jpeg"):
    return client.post("/api/v1/files", headers=_h(tokens),
                       files={"file": (name, body, mime)}, data={"kind": kind})


def _chat(client, alice, bob) -> dict:
    bob_id = client.get("/api/v1/users/me", headers=_h(bob)).json()["id"]
    return client.post("/api/v1/chats", headers=_h(alice),
                       json={"type": "private", "member_ids": [bob_id]}).json()


# --------------------------------------------------------------------------- #
# validation
# --------------------------------------------------------------------------- #

def test_photo_upload_is_sniffed_and_stored(sync_client: TestClient):
    alice = _register(sync_client)
    response = _upload(sync_client, alice, JPEG)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["mime_type"] == "image/jpeg"
    assert body["size"] == len(JPEG) and body["kind"] == "photo"
    assert body["sha256"]


def test_declared_type_lying_about_content_is_rejected(sync_client: TestClient):
    alice = _register(sync_client)
    # PNG bytes uploaded as a video
    as_video = _upload(sync_client, alice, PNG, kind="video", name="clip.mp4", mime="video/mp4")
    assert as_video.status_code == 415, as_video.text
    # executable bytes uploaded as a photo
    as_photo = _upload(sync_client, alice, ELF, kind="photo")
    assert as_photo.status_code == 415, as_photo.text


def test_filename_is_sanitised(sync_client: TestClient):
    alice = _register(sync_client)
    hostile = 'evil"\r\nX-Injected: 1.jpg'
    response = _upload(sync_client, alice, JPEG, name=hostile)
    assert response.status_code == 201, response.text
    stored_name = response.json()["name"]
    assert "\r" not in stored_name and "\n" not in stored_name and '"' not in stored_name


def test_same_bytes_are_not_stored_twice(sync_client: TestClient):
    alice = _register(sync_client)
    first = _upload(sync_client, alice, JPEG).json()
    second = _upload(sync_client, alice, JPEG).json()
    assert first["id"] == second["id"]
    assert first["sha256"] == second["sha256"]


def test_unknown_kind_is_rejected(sync_client: TestClient):
    alice = _register(sync_client)
    response = _upload(sync_client, alice, JPEG, kind="malware")
    assert response.status_code in (415, 422)


# --------------------------------------------------------------------------- #
# access control
# --------------------------------------------------------------------------- #

def test_members_download_and_outsiders_are_refused(sync_client: TestClient):
    alice, bob = _register(sync_client), _register(sync_client)
    carol = _register(sync_client)
    chat = _chat(sync_client, alice, bob)
    file_id = _upload(sync_client, alice, JPEG).json()["id"]
    sent = sync_client.post(f"/api/v1/chats/{chat['id']}/messages", headers=_h(alice),
                            json={"type": "photo", "file_id": file_id, "text": "عکس"})
    assert sent.status_code == 201, sent.text

    assert sync_client.get(f"/api/v1/files/{file_id}/content", headers=_h(bob)).status_code == 200
    assert sync_client.get(f"/api/v1/files/{file_id}/content", headers=_h(alice)).status_code == 200
    denied = sync_client.get(f"/api/v1/files/{file_id}/content", headers=_h(carol))
    assert denied.status_code == 403
    # the download URL never reveals the storage path
    payload = sync_client.get(f"/api/v1/files/{file_id}", headers=_h(bob)).json()
    assert "/storage/" not in payload["download_url"] and ".." not in payload["download_url"]


def test_access_ends_when_the_message_is_deleted(sync_client: TestClient):
    alice, bob = _register(sync_client), _register(sync_client)
    chat = _chat(sync_client, alice, bob)
    file_id = _upload(sync_client, alice, JPEG).json()["id"]
    message = sync_client.post(f"/api/v1/chats/{chat['id']}/messages", headers=_h(alice),
                               json={"type": "photo", "file_id": file_id}).json()
    assert sync_client.get(f"/api/v1/files/{file_id}/content", headers=_h(bob)).status_code == 200

    assert sync_client.delete(f"/api/v1/messages/{message['id']}",
                              headers=_h(alice)).status_code == 204
    assert sync_client.get(f"/api/v1/files/{file_id}/content", headers=_h(bob)).status_code == 403


# --------------------------------------------------------------------------- #
# multipart
# --------------------------------------------------------------------------- #

def test_multipart_upload_assembles_the_file(sync_client: TestClient):
    alice = _register(sync_client)
    payload = JPEG + b"second-part-body"
    init = sync_client.post("/api/v1/files/multipart/init", headers=_h(alice),
                            json={"name": "big.jpg", "kind": "photo", "size": len(payload),
                                  "mime_type": "image/jpeg"})
    assert init.status_code == 201, init.text
    upload_id = init.json()["upload_id"]

    assert sync_client.put(f"/api/v1/files/multipart/{upload_id}/parts/0",
                           headers=_h(alice), content=payload[:60]).status_code == 200
    part_two = sync_client.put(f"/api/v1/files/multipart/{upload_id}/parts/1",
                               headers=_h(alice), content=payload[60:])
    assert part_two.status_code == 200

    done = sync_client.post(f"/api/v1/files/multipart/{upload_id}/complete", headers=_h(alice))
    assert done.status_code == 201, done.text
    body = done.json()
    assert body["size"] == len(payload) and body["mime_type"] == "image/jpeg"
    assert sync_client.get(f"/api/v1/files/{body['id']}/content",
                           headers=_h(alice)).content == payload


def test_multipart_upload_is_owner_only(sync_client: TestClient):
    alice, bob = _register(sync_client), _register(sync_client)
    init = sync_client.post("/api/v1/files/multipart/init", headers=_h(alice),
                            json={"name": "x.jpg", "kind": "photo", "size": 20}).json()
    upload_id = init["upload_id"]
    assert sync_client.put(f"/api/v1/files/multipart/{upload_id}/parts/0",
                           headers=_h(bob), content=b"\xff\xd8\xff" + b"0" * 17).status_code == 403
    assert sync_client.post(f"/api/v1/files/multipart/{upload_id}/complete",
                            headers=_h(bob)).status_code == 403


def test_multipart_size_mismatch_is_rejected(sync_client: TestClient):
    alice = _register(sync_client)
    init = sync_client.post("/api/v1/files/multipart/init", headers=_h(alice),
                            json={"name": "y.jpg", "kind": "photo", "size": 500}).json()
    upload_id = init["upload_id"]
    sync_client.put(f"/api/v1/files/multipart/{upload_id}/parts/0", headers=_h(alice),
                    content=JPEG)
    assert sync_client.post(f"/api/v1/files/multipart/{upload_id}/complete",
                            headers=_h(alice)).status_code == 400


def test_multipart_respects_the_size_limit(sync_client: TestClient):
    alice = _register(sync_client)
    too_big = sync_client.post("/api/v1/files/multipart/init", headers=_h(alice),
                               json={"name": "z.bin", "kind": "document",
                                     "size": 400 * 1024 * 1024})
    assert too_big.status_code == 413


# --------------------------------------------------------------------------- #
# realtime + lifecycle
# --------------------------------------------------------------------------- #

def test_upload_and_media_events_reach_the_right_people(sync_client: TestClient):
    alice, bob = _register(sync_client), _register(sync_client)
    chat = _chat(sync_client, alice, bob)

    with sync_client.websocket_connect(f"/api/v1/ws?token={alice['access_token']}") as alice_ws:
        assert alice_ws.receive_json()["type"] == "ready"
        file_id = _upload(sync_client, alice, JPEG).json()["id"]
        seen = []
        for _ in range(6):
            frame = alice_ws.receive_json()
            seen.append(frame["type"])
            if "media.uploaded" in seen:
                break
        assert "media.uploaded" in seen, seen

    with sync_client.websocket_connect(f"/api/v1/ws?token={bob['access_token']}") as bob_ws:
        assert bob_ws.receive_json()["type"] == "ready"
        sync_client.post(f"/api/v1/chats/{chat['id']}/messages", headers=_h(alice),
                         json={"type": "photo", "file_id": file_id})
        seen = []
        for _ in range(10):
            frame = bob_ws.receive_json()
            seen.append(frame["type"])
            if "message.media" in seen and "media.ready" in seen:
                break
        assert "message.new" in seen and "message.media" in seen and "media.ready" in seen, seen


def test_deleting_a_file_notifies_the_chats(sync_client: TestClient):
    alice, bob = _register(sync_client), _register(sync_client)
    chat = _chat(sync_client, alice, bob)
    file_id = _upload(sync_client, alice, JPEG).json()["id"]
    sync_client.post(f"/api/v1/chats/{chat['id']}/messages", headers=_h(alice),
                     json={"type": "photo", "file_id": file_id})

    with sync_client.websocket_connect(f"/api/v1/ws?token={bob['access_token']}") as bob_ws:
        assert bob_ws.receive_json()["type"] == "ready"
        assert sync_client.delete(f"/api/v1/files/{file_id}", headers=_h(alice)).status_code == 204
        seen = []
        for _ in range(8):
            frame = bob_ws.receive_json()
            seen.append(frame["type"])
            if "media.deleted" in seen:
                break
        assert "media.deleted" in seen, seen
