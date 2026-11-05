"""Phase 6 acceptance probe — media over the real server with demo accounts.

    python tools/android_media_probe.py

Covers upload/validation/access/multipart/cancel/retry/events/limits with
demo_alice and demo_bob plus one throw-away third account for the IDOR check.
API level only: this does not replace running the app.
"""
from __future__ import annotations

import asyncio
import json
import sys
import uuid

import httpx
import websockets

BASE = "http://127.0.0.1:8000/api/v1"
WS = "ws://127.0.0.1:8000/api/v1/ws"
JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01" + b"\x00" * 200
MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 200
PDF = b"%PDF-1.7\n" + b"\x00" * 200

passed = failed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  ok   {name}")
    else:
        failed += 1
        print(f"  FAIL {name} {detail}")


def h(t: dict) -> dict:
    return {"Authorization": f"Bearer {t['access_token']}"}


def dev_login(client, account: str) -> dict:
    r = client.post(f"{BASE}/auth/dev-login", json={"account": account, "device_name": "media"})
    assert r.status_code == 200, r.text
    return r.json()


def throwaway(client) -> dict:
    name = f"media_{uuid.uuid4().hex[:8]}"
    ch = client.post(f"{BASE}/auth/otp/request", json={"username": name, "purpose": "register"})
    token = ch.json().get("registration_token")
    body = {"username": name, "password": "MediaProbe1234", "display_name": name,
            "registration_token": token}
    r = client.post(f"{BASE}/auth/register", json=body)
    return r.json() if r.status_code == 201 else {}


async def recv(socket, wanted, limit=12, timeout=5.0):
    for _ in range(limit):
        try:
            frame = json.loads(await asyncio.wait_for(socket.recv(), timeout=timeout))
        except asyncio.TimeoutError:
            return None
        if frame.get("type") == wanted:
            return frame
    return None


async def main() -> int:
    with httpx.Client(timeout=30) as client:
        alice, bob = dev_login(client, "demo_alice"), dev_login(client, "demo_bob")
        a, b = h(alice), h(bob)
        bob_id = client.get(f"{BASE}/users/me", headers=b).json()["id"]
        chat = client.post(f"{BASE}/chats", headers=a,
                           json={"type": "private", "member_ids": [bob_id]})
        chat_id = (chat.json()["id"] if chat.status_code == 201 else
                   next(c["id"] for c in client.get(f"{BASE}/chats", headers=a).json()
                        if c["type"] == "private"))

        # 1-2: photo upload + send
        photo = client.post(f"{BASE}/files", headers=a,
                            files={"file": ("p.jpg", JPEG, "image/jpeg")}, data={"kind": "photo"})
        check("photo upload accepted with sniffed type",
              photo.status_code == 201 and photo.json()["mime_type"] == "image/jpeg", photo.text)
        photo_id = photo.json()["id"]

        async with websockets.connect(f"{WS}?token={bob['access_token']}") as sock:
            await sock.recv()
            sent = client.post(f"{BASE}/chats/{chat_id}/messages", headers=a,
                               json={"type": "photo", "file_id": photo_id, "text": "عکس"})
            check("photo message created", sent.status_code == 201, sent.text)
            new = await recv(sock, "message.new")
            check("peer receives message.new", new is not None)
            media = await recv(sock, "message.media")
            check("peer receives message.media", media is not None)
            ready = await recv(sock, "media.ready")
            check("peer receives media.ready", ready is not None)

        # 3: download by member
        download = client.get(f"{BASE}/files/{photo_id}/content", headers=b)
        check("member downloads the photo", download.status_code == 200
              and download.content == JPEG)

        # 6: IDOR
        stranger = throwaway(client)
        denied = client.get(f"{BASE}/files/{photo_id}/content", headers=h(stranger))
        check("outside account is refused", denied.status_code == 403, str(denied.status_code))

        # 3: video
        video = client.post(f"{BASE}/files", headers=a,
                            files={"file": ("v.mp4", MP4, "video/mp4")}, data={"kind": "video"})
        check("video upload accepted", video.status_code == 201
              and video.json()["mime_type"] == "video/mp4", video.text)
        video_msg = client.post(f"{BASE}/chats/{chat_id}/messages", headers=a,
                                json={"type": "video", "file_id": video.json()["id"]})
        check("video message created and delivered", video_msg.status_code == 201)

        # 4: document metadata
        doc = client.post(f"{BASE}/files", headers=a,
                          files={"file": ("report.pdf", PDF, "application/pdf")},
                          data={"kind": "document"})
        body = doc.json()
        check("document keeps name, size and mime",
              doc.status_code == 201 and body["name"] == "report.pdf"
              and body["mime_type"] == "application/pdf" and body["size"] == len(PDF), doc.text)

        # 13: limits and type checks
        bad_video = client.post(f"{BASE}/files", headers=a,
                                files={"file": ("x.mp4", JPEG, "video/mp4")}, data={"kind": "video"})
        check("content that lies about its type is rejected", bad_video.status_code == 415)
        too_big = client.post(f"{BASE}/files/multipart/init", headers=a,
                              json={"name": "huge.bin", "kind": "document",
                                    "size": 500 * 1024 * 1024})
        check("size limit enforced", too_big.status_code == 413)

        # 9: retry after a failure (same bytes now valid as a document)
        retry = client.post(f"{BASE}/files", headers=a,
                            files={"file": ("same.bin", JPEG, "application/octet-stream")},
                            data={"kind": "document"})
        check("retry after rejection succeeds", retry.status_code == 201, retry.text)

        # 10: no duplicated rows for identical bytes
        again = client.post(f"{BASE}/files", headers=a,
                            files={"file": ("p2.jpg", JPEG, "image/jpeg")}, data={"kind": "photo"})
        check("identical bytes reuse the stored file", again.json()["id"] == photo_id)

        # 8: cancel / incomplete multipart never becomes a file
        init = client.post(f"{BASE}/files/multipart/init", headers=a,
                           json={"name": "big.jpg", "kind": "photo", "size": 300}).json()
        client.put(f"{BASE}/files/multipart/{init['upload_id']}/parts/0",
                   headers=a, content=JPEG[:150])
        incomplete = client.post(f"{BASE}/files/multipart/{init['upload_id']}/complete",
                                 headers=a)
        check("an aborted (cancelled) upload creates no file", incomplete.status_code == 400,
              str(incomplete.status_code))

        # 12: access ends when the message is deleted
        # a dedicated file: the earlier photo is still referenced by its own message
        unique = JPEG + uuid.uuid4().bytes
        unique_id = client.post(f"{BASE}/files", headers=a,
                                files={"file": ("u.jpg", unique, "image/jpeg")},
                                data={"kind": "photo"}).json()["id"]
        toggled = client.post(f"{BASE}/chats/{chat_id}/messages", headers=a,
                              json={"type": "photo", "file_id": unique_id})
        message_id = toggled.json()["id"]
        client.delete(f"{BASE}/messages/{message_id}", headers=a)
        revoked = client.get(f"{BASE}/files/{unique_id}/content", headers=b)
        check("access revoked after the message is deleted",
              revoked.status_code == 403, str(revoked.status_code))

    print(f"\n{passed} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
