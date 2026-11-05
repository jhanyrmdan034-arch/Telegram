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


# --------------------------------------------------------------------------- #
# download tokens, public flag, upload budget
# --------------------------------------------------------------------------- #

def test_download_token_grants_file_scoped_access(sync_client: TestClient):
    alice, bob = _register(sync_client), _register(sync_client)
    carol = _register(sync_client)
    chat = _chat(sync_client, alice, bob)
    file_id = _upload(sync_client, alice, JPEG).json()["id"]
    other_id = _upload(sync_client, alice, JPEG + b"-other").json()["id"]
    sync_client.post(f"/api/v1/chats/{chat['id']}/messages", headers=_h(alice),
                     json={"type": "photo", "file_id": file_id})

    token = sync_client.post(f"/api/v1/files/{file_id}/download-token", headers=_h(bob))
    assert token.status_code == 200, token.text
    from urllib.parse import urlparse

    url = token.json()["url"]
    parsed = urlparse(url)
    streamed = sync_client.get(f"{parsed.path}?{parsed.query}")
    assert streamed.status_code == 200 and streamed.content == JPEG

    # A token for one file must not open another file.
    swapped = urlparse(token.json()["url"])
    assert sync_client.get(f"{swapped.path.replace(str(file_id), str(other_id))}?"
                           f"{swapped.query}").status_code == 403
    # Nor may an outsider obtain one.
    assert sync_client.post(f"/api/v1/files/{file_id}/download-token",
                            headers=_h(carol)).status_code == 403
    # Tampering with the signature is refused.
    tampered = urlparse(token.json()["url"][:-3] + "abc")
    assert sync_client.get(f"{tampered.path}?{tampered.query}").status_code == 403


def test_expired_download_token_is_refused(sync_client: TestClient):
    alice = _register(sync_client)
    file_id = _upload(sync_client, alice, JPEG).json()["id"]
    runtime = sync_client.app.state.runtime
    original = runtime.settings.download_token_ttl_seconds
    runtime.settings.download_token_ttl_seconds = -5
    try:
        raw = sync_client.post(f"/api/v1/files/{file_id}/download-token",
                               headers=_h(alice)).json()["url"]
    finally:
        runtime.settings.download_token_ttl_seconds = original
    from urllib.parse import urlparse

    parsed = urlparse(raw)
    assert sync_client.get(f"{parsed.path}?{parsed.query}").status_code == 403


def test_public_flag_does_not_bypass_ownership_when_disabled():
    import asyncio
    from types import SimpleNamespace

    from app.routers.files import _can_access

    class _Db:
        async def execute(self, *args, **kwargs):
            return SimpleNamespace(scalar_one_or_none=lambda: None)

    runtime = SimpleNamespace(settings=SimpleNamespace(public_files_enabled=False))
    record = SimpleNamespace(is_public=True, owner_id=uuid.uuid4(), id=uuid.uuid4())
    assert asyncio.run(_can_access(_Db(), record, uuid.uuid4(), runtime)) is False

    permissive = SimpleNamespace(settings=SimpleNamespace(public_files_enabled=True))
    assert asyncio.run(_can_access(_Db(), record, uuid.uuid4(), permissive)) is True


def test_upload_budget_is_per_account(sync_client: TestClient):
    alice = _register(sync_client)
    runtime = sync_client.app.state.runtime
    original = runtime.settings.upload_rate_limit_requests
    runtime.settings.upload_rate_limit_requests = 2
    try:
        codes = [_upload(sync_client, alice, JPEG + bytes([i])).status_code for i in range(4)]
    finally:
        runtime.settings.upload_rate_limit_requests = original
    assert codes[:2] == [201, 201], codes
    assert 429 in codes[2:], codes


# --------------------------------------------------------------------------- #
# interrupted upload / resume / duplicate protection
# --------------------------------------------------------------------------- #

def test_interrupted_upload_can_be_resumed(sync_client: TestClient):
    alice = _register(sync_client)
    payload = JPEG + b"payload-after-drop" * 4
    init = sync_client.post("/api/v1/files/multipart/init", headers=_h(alice),
                            json={"name": "resume.jpg", "kind": "photo",
                                  "size": len(payload)}).json();
    upload_id = init["upload_id"]

    # The connection drops after the first part: completing now must fail and
    # leave no half file behind.
    sync_client.put(f"/api/v1/files/multipart/{upload_id}/parts/0", headers=_h(alice),
                    content=payload[:100])
    assert sync_client.post(f"/api/v1/files/multipart/{upload_id}/complete",
                            headers=_h(alice)).status_code == 400

    # Reconnect: resend the remainder, then the upload completes exactly once.
    sync_client.put(f"/api/v1/files/multipart/{upload_id}/parts/1", headers=_h(alice),
                    content=payload[100:])
    done = sync_client.post(f"/api/v1/files/multipart/{upload_id}/complete", headers=_h(alice))
    assert done.status_code == 201, done.text
    assert sync_client.get(f"/api/v1/files/{done.json()['id']}/content",
                           headers=_h(alice)).content == payload


def test_duplicate_submission_creates_a_single_media_message(sync_client: TestClient):
    alice, bob = _register(sync_client), _register(sync_client)
    chat = _chat(sync_client, alice, bob)
    file_id = _upload(sync_client, alice, JPEG).json()["id"]
    body = {"type": "photo", "file_id": file_id, "client_message_id": "media-dup-1"}
    first = sync_client.post(f"/api/v1/chats/{chat['id']}/messages", headers=_h(alice), json=body)
    second = sync_client.post(f"/api/v1/chats/{chat['id']}/messages", headers=_h(alice), json=body)
    assert first.status_code == 201 and second.status_code == 201
    assert first.json()["id"] == second.json()["id"]

    history = sync_client.get(f"/api/v1/chats/{chat['id']}/messages", headers=_h(bob)).json()
    items = history["items"] if isinstance(history, dict) else history
    assert len([m for m in items if m.get("file_id") == file_id]) == 1


def test_failed_media_raises_a_websocket_event(sync_client: TestClient):
    alice = _register(sync_client)
    with sync_client.websocket_connect(f"/api/v1/ws?token={alice['access_token']}") as ws:
        assert ws.receive_json()["type"] == "ready"
        denied = _upload(sync_client, alice, PNG, kind="video", name="clip.mp4",
                         mime="video/mp4")
        assert denied.status_code == 415
        seen = []
        for _ in range(8):
            frame = ws.receive_json()
            seen.append(frame["type"])
            if "media.failed" in seen:
                break
        assert "media.failed" in seen, seen


def test_message_carries_file_metadata_for_the_bubble(sync_client: TestClient):
    alice, bob = _register(sync_client), _register(sync_client)
    chat = _chat(sync_client, alice, bob)
    # a real 1x1 PNG so the server can record dimensions
    one_pixel = bytes.fromhex(
        "89504e470d0a1a0a0000000d494844520000000100000001080600000"
        "01f15c4890000000a49444154789c6300010000050001"
        "0d0a2db40000000049454e44ae426082"
    )
    uploaded = _upload(sync_client, alice, one_pixel, name="dot.png", mime="image/png").json()
    assert uploaded["mime_type"] == "image/png"
    assert uploaded["width"] == 1 and uploaded["height"] == 1

    sync_client.post(f"/api/v1/chats/{chat['id']}/messages", headers=_h(alice),
                     json={"type": "photo", "file_id": uploaded["id"], "text": "نقطه"})
    page = sync_client.get(f"/api/v1/chats/{chat['id']}/messages", headers=_h(bob)).json()
    media = [item for item in page["items"] if item.get("file")]
    assert media, page
    file_meta = media[0]["file"]
    assert file_meta["name"] == "dot.png"
    assert file_meta["size"] == len(one_pixel)
    assert file_meta["mime_type"] == "image/png"
    assert file_meta["width"] == 1 and file_meta["height"] == 1


# --------------------------------------------------------------------------- #
# Phase 6.1 — security re-review
# --------------------------------------------------------------------------- #

def test_identical_bytes_from_two_accounts_stay_independent(sync_client: TestClient):
    alice, bob = _register(sync_client), _register(sync_client)
    first = _upload(sync_client, alice, JPEG).json()
    second = _upload(sync_client, bob, JPEG).json()
    # Separate records (different owners) but one content-addressed object.
    assert first["id"] != second["id"]
    assert first["sha256"] == second["sha256"]

    # Deleting Alice's copy must not destroy Bob's file.
    assert sync_client.delete(f"/api/v1/files/{first['id']}", headers=_h(alice)).status_code == 204
    kept = sync_client.get(f"/api/v1/files/{second['id']}/content", headers=_h(bob))
    assert kept.status_code == 200 and kept.content == JPEG


def test_download_token_can_travel_in_a_header(sync_client: TestClient):
    alice = _register(sync_client)
    file_id = _upload(sync_client, alice, JPEG).json()["id"]
    token = sync_client.post(f"/api/v1/files/{file_id}/download-token",
                             headers=_h(alice)).json()["token"]
    # No token in the URL at all: nothing sensitive reaches an access log.
    response = sync_client.get(f"/api/v1/files/{file_id}/stream",
                               headers={"X-Download-Token": token})
    assert response.status_code == 200 and response.content == JPEG
    assert sync_client.get(f"/api/v1/files/{file_id}/stream").status_code == 403


def test_gc_keeps_live_files_and_collects_orphans():
    from datetime import UTC, datetime, timedelta
    from types import SimpleNamespace

    from app.services import is_orphan_file

    now = datetime.now(UTC)
    cutoff = now - timedelta(hours=24)
    old = now - timedelta(hours=48)
    live_id, orphan_id, young_id, public_id = (uuid.uuid4() for _ in range(4))

    live = SimpleNamespace(id=live_id, is_public=False, created_at=old)
    orphan = SimpleNamespace(id=orphan_id, is_public=False, created_at=old)
    young = SimpleNamespace(id=young_id, is_public=False, created_at=now)
    public = SimpleNamespace(id=public_id, is_public=True, created_at=old)

    referenced = {live_id}
    assert is_orphan_file(live, referenced, cutoff) is False
    assert is_orphan_file(orphan, referenced, cutoff) is True
    assert is_orphan_file(young, referenced, cutoff) is False
    assert is_orphan_file(public, referenced, cutoff) is False


def test_access_is_revoked_after_message_deletion_and_file_deletion(sync_client: TestClient):
    alice, bob = _register(sync_client), _register(sync_client)
    chat = _chat(sync_client, alice, bob)
    file_id = _upload(sync_client, alice, JPEG + b"-revoke").json()["id"]
    message = sync_client.post(f"/api/v1/chats/{chat['id']}/messages", headers=_h(alice),
                               json={"type": "photo", "file_id": file_id}).json()
    sync_client.delete(f"/api/v1/messages/{message['id']}", headers=_h(alice))
    assert sync_client.get(f"/api/v1/files/{file_id}/content", headers=_h(bob)).status_code == 403
    assert sync_client.delete(f"/api/v1/files/{file_id}", headers=_h(alice)).status_code == 204
    assert sync_client.get(f"/api/v1/files/{file_id}/content", headers=_h(alice)).status_code == 404
