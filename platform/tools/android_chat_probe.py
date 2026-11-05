"""Phase 4 acceptance probe — the chat screen contract with the two demo accounts.

Checks, against a live server (DEV_LOGIN_ENABLED=true):

    python tools/android_chat_probe.py

Covers: send/receive over a real WebSocket, delivery + read receipts, edit,
delete for both sides, typing, in-chat search, idempotent retry and recovery of
messages missed while the socket was down.

This is an API-level probe. It does not replace running the app.
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

passed = failed = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global passed, failed
    if condition:
        passed += 1
        print(f"  ok   {name}")
    else:
        failed += 1
        print(f"  FAIL {name} {detail}")


def dev_login(client: httpx.Client, account: str) -> dict:
    response = client.post(f"{BASE}/auth/dev-login",
                           json={"account": account, "device_name": "chat-probe"})
    assert response.status_code == 200, response.text
    return response.json()


def h(tokens: dict) -> dict:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


async def recv_until(socket, wanted: str, limit: int = 8, timeout: float = 4.0):
    for _ in range(limit):
        try:
            frame = json.loads(await asyncio.wait_for(socket.recv(), timeout=timeout))
        except asyncio.TimeoutError:
            return None
        if frame.get("type") == wanted:
            return frame
    return None


async def main() -> int:
    with httpx.Client(timeout=20) as client:
        alice = dev_login(client, "demo_alice")
        bob = dev_login(client, "demo_bob")
        a, b = h(alice), h(bob)
        alice_id = client.get(f"{BASE}/users/me", headers=a).json()["id"]
        bob_id = client.get(f"{BASE}/users/me", headers=b).json()["id"]

        chat = client.post(f"{BASE}/chats", headers=a,
                           json={"type": "private", "member_ids": [bob_id]})
        if chat.status_code == 409:
            chat_id = next(c["id"] for c in client.get(f"{BASE}/chats", headers=a).json()
                           if c["type"] == "private")
        else:
            chat_id = chat.json()["id"]

        mark = uuid.uuid4().hex[:6]

        # ---- 1. send / receive over a real socket -------------------------
        async with websockets.connect(f"{WS}?token={bob['access_token']}") as bob_socket, \
                websockets.connect(f"{WS}?token={alice['access_token']}") as alice_socket:
            await bob_socket.recv()
            await alice_socket.recv()
            sent = client.post(f"{BASE}/chats/{chat_id}/messages", headers=a,
                               json={"text": f"سلام {mark}",
                                     "client_message_id": f"c-{mark}"})
            assert sent.status_code == 201, sent.text
            message = sent.json()

            frame = await recv_until(bob_socket, "message.new")
            check("message.new reaches the peer",
                  frame is not None and frame["data"]["text"] == f"سلام {mark}")

            # ---- 2. typing -------------------------------------------------
            client.post(f"{BASE}/chats/{chat_id}/typing", headers=a)
            typing = await recv_until(bob_socket, "typing")
            check("typing event carries chat and sender",
                  typing is not None and typing["data"]["chat_id"] == chat_id
                  and typing["data"]["user_id"] == alice_id)

            # ---- 3. read receipt toward the sender -------------------------
            bob_row = next(r for r in client.get(f"{BASE}/chats", headers=b).json()
                           if r["id"] == chat_id)
            check("delivery: unread counter visible to the receiver",
                  bob_row["unread_count"] >= 1)
            client.post(f"{BASE}/chats/{chat_id}/read", headers=b,
                        params={"seq": bob_row["last_seq"]})
            receipt = await recv_until(alice_socket, "read.updated")
            check("read.updated is broadcast to the other side",
                  receipt is not None and receipt["data"]["user_id"] == bob_id)

            # ---- 4. edit ---------------------------------------------------
            edited = client.patch(f"{BASE}/messages/{message['id']}", headers=a,
                                  json={"text": f"ویرایش‌شده {mark}"})
            check("edit is stored", edited.status_code == 200
                  and edited.json()["text"] == f"ویرایش‌شده {mark}"
                  and edited.json()["edited_at"])
            edit_frame = await recv_until(bob_socket, "message.edited")
            check("message.edited reaches the peer",
                  edit_frame is not None and edit_frame["data"]["text"] == f"ویرایش‌شده {mark}")

            # ---- 5. reply to a message -------------------------------------
            reply = client.post(f"{BASE}/chats/{chat_id}/messages", headers=b,
                                json={"text": "پاسخ", "reply_to_id": message["id"]})
            check("reply stores its parent",
                  reply.status_code == 201 and reply.json()["reply_to_id"] == message["id"])

            # ---- 6. delete for both sides ----------------------------------
            doomed = client.post(f"{BASE}/chats/{chat_id}/messages", headers=a,
                                 json={"text": f"حذف {mark}"}).json()
            deleted = client.delete(f"{BASE}/messages/{doomed['id']}", headers=a)
            gone = all(m["id"] != doomed["id"] for m in client.get(
                f"{BASE}/chats/{chat_id}/messages", headers=b).json()["items"])
            check("delete removes the message for the receiver too",
                  deleted.status_code == 204 and gone)
            delete_frame = await recv_until(bob_socket, "message.deleted")
            check("message.deleted reaches the peer", delete_frame is not None)

        # ---- 7. idempotent retry ------------------------------------------
        client_id = f"retry-{mark}"
        first = client.post(f"{BASE}/chats/{chat_id}/messages", headers=a,
                            json={"text": "تلاش", "client_message_id": client_id}).json()
        second = client.post(f"{BASE}/chats/{chat_id}/messages", headers=a,
                             json={"text": "تلاش", "client_message_id": client_id}).json()
        check("a retried send does not duplicate", first["id"] == second["id"])

        # ---- 8. search inside the chat ------------------------------------
        found = client.get(f"{BASE}/chats/{chat_id}/messages/search", headers=a,
                           params={"q": mark}).json()
        check("in-chat search finds the marked messages", len(found) >= 1)

        # ---- 9. recovery after a dropped connection -----------------------
        row = next(r for r in client.get(f"{BASE}/chats", headers=b).json()
                   if r["id"] == chat_id)
        seen_seq = row["last_seq"]
        client.post(f"{BASE}/chats/{chat_id}/messages", headers=a,
                    json={"text": f"missed-1 {mark}"})
        client.post(f"{BASE}/chats/{chat_id}/messages", headers=a,
                    json={"text": f"missed-2 {mark}"})
        missed = client.get(f"{BASE}/chats/{chat_id}/messages", headers=b,
                            params={"after_seq": seen_seq}).json()["items"]
        texts = [m["text"] for m in missed]
        check("messages sent while offline are recovered exactly once",
              texts.count(f"missed-1 {mark}") == 1 and texts.count(f"missed-2 {mark}") == 1,
              str(texts))

    print(f"\n{passed} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
