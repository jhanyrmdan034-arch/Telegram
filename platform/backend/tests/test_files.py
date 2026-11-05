"""File upload, download and access-control tests."""
from __future__ import annotations

from tests.conftest import auth, register_user

PDF_BYTES = b"%PDF-1.4 fake test payload"


async def test_upload_metadata_and_download(client, alice):
    upload = await client.post(
        "/api/v1/files",
        headers=auth(alice["access_token"]),
        files={"file": ("note.pdf", PDF_BYTES, "application/pdf")},
        data={"kind": "document"},
    )
    assert upload.status_code == 201, upload.text
    body = upload.json()
    assert body["size"] == len(PDF_BYTES)
    assert body["sha256"] == __import__("hashlib").sha256(PDF_BYTES).hexdigest()
    assert body["download_url"].endswith(f"/api/v1/files/{body['id']}/content")

    download = await client.get(f"/api/v1/files/{body['id']}/content",
                                headers=auth(alice["access_token"]))
    assert download.status_code == 200
    assert download.content == PDF_BYTES
    assert download.headers["content-type"].startswith("application/pdf")


async def test_content_addressing_deduplicates(client, alice):
    payload = {"kind": "document"}
    first = (await client.post("/api/v1/files", headers=auth(alice["access_token"]),
                               files={"file": ("a.txt", b"same bytes", "text/plain")},
                               data=payload)).json()
    second = (await client.post("/api/v1/files", headers=auth(alice["access_token"]),
                                files={"file": ("b.txt", b"same bytes", "text/plain")},
                                data=payload)).json()
    # Phase 6: the same owner re-uploading the same bytes gets the same record back
    # (duplicate-upload avoidance), and only one object exists on disk.
    assert first["id"] == second["id"]
    assert first["sha256"] == second["sha256"]

    from pathlib import Path

    stored = list(Path(client._transport.app.state.runtime.storage.root).rglob(first["sha256"]))
    assert len(stored) == 1


async def test_other_users_cannot_download_private_file(client, alice, bob):
    upload = (await client.post("/api/v1/files", headers=auth(alice["access_token"]),
                                files={"file": ("secret.txt", b"private", "text/plain")},
                                data={"kind": "document"})).json()

    denied = await client.get(f"/api/v1/files/{upload['id']}/content",
                              headers=auth(bob["access_token"]))
    assert denied.status_code == 403

    eve = await register_user(client, "eve_files")
    assert (await client.get(f"/api/v1/files/{upload['id']}/content",
                             headers=auth(eve["access_token"]))).status_code == 403


async def test_sharing_file_in_chat_grants_members_access(client, alice, bob):
    bob_id = (await client.get("/api/v1/users/me", headers=auth(bob["access_token"]))).json()["id"]
    chat = (await client.post("/api/v1/chats", headers=auth(alice["access_token"]),
                              json={"type": "private", "member_ids": [bob_id]})).json()

    upload = (await client.post("/api/v1/files", headers=auth(alice["access_token"]),
                                files={"file": ("photo.jpg", b"\xff\xd8\xff", "image/jpeg")},
                                data={"kind": "photo"})).json()

    sent = await client.post(f"/api/v1/chats/{chat['id']}/messages",
                             headers=auth(alice["access_token"]),
                             json={"type": "photo", "file_id": upload["id"]})
    assert sent.status_code == 201

    allowed = await client.get(f"/api/v1/files/{upload['id']}/content",
                               headers=auth(bob["access_token"]))
    assert allowed.status_code == 200
    assert allowed.content == b"\xff\xd8\xff"


async def test_size_limit_enforced(client, alice):
    oversized = b"x" * (1024 * 1024 + 10)  # settings cap is 1 MiB in tests
    response = await client.post("/api/v1/files", headers=auth(alice["access_token"]),
                                 files={"file": ("big.bin", oversized, "application/octet-stream")},
                                 data={"kind": "document"})
    assert response.status_code == 413


async def test_empty_file_rejected(client, alice):
    response = await client.post("/api/v1/files", headers=auth(alice["access_token"]),
                                 files={"file": ("empty.txt", b"", "text/plain")},
                                 data={"kind": "document"})
    assert response.status_code == 400


async def test_invalid_kind_rejected(client, alice):
    response = await client.post("/api/v1/files", headers=auth(alice["access_token"]),
                                 files={"file": ("a.txt", b"data", "text/plain")},
                                 data={"kind": "malware"})
    assert response.status_code == 422


async def test_delete_file_removes_object(client, alice, storage_dir):
    upload = (await client.post("/api/v1/files", headers=auth(alice["access_token"]),
                                files={"file": ("gone.txt", b"bye", "text/plain")},
                                data={"kind": "document"})).json()

    deleted = await client.delete(f"/api/v1/files/{upload['id']}",
                                  headers=auth(alice["access_token"]))
    assert deleted.status_code == 204
    assert (await client.get(f"/api/v1/files/{upload['id']}",
                             headers=auth(alice["access_token"]))).status_code == 404
    assert storage_dir.exists()


async def test_concurrent_inserts_of_the_same_file_converge(client, alice):
    """Two identical uploads racing must end up as one record (unique constraint)."""
    import asyncio
    import uuid as _uuid

    from app.models import File
    from app.routers.files import _insert_or_reuse

    runtime = client._transport.app.state.runtime
    owner_id = _uuid.UUID(alice["user"]["id"]) if "user" in alice else _uuid.UUID(
        (await client.get("/api/v1/users/me", headers=auth(alice["access_token"]))).json()["id"])
    digest = "c" * 64

    async def insert():
        async with runtime.session_factory() as session:
            record = File(owner_id=owner_id, storage_key=digest, name="race.bin",
                          mime_type="application/octet-stream", size=4, sha256=digest,
                          kind="other")
            return await _insert_or_reuse(session, runtime, record, owner_id, "other")

    first, second = await asyncio.gather(insert(), insert())
    assert first.id == second.id
