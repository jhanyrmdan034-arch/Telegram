"""Phase 3 acceptance probe — the dialog list as DialogsActivity sees it.

Runs against a live server with DEV_LOGIN_ENABLED=true and checks, with the two
demo accounts, every field and action the main screen uses:

    python tools/android_dialogs_probe.py            (server on :8000)

It mirrors the client's request sequence (dev-login -> GET /chats -> WS events ->
actions), so a green run means the contract DialogsActivity depends on holds on
the real database, not only in unit tests.
"""
from __future__ import annotations

import asyncio
import json
import sys

import httpx
import websockets

BASE = "http://127.0.0.1:8000/api/v1"
WS = "ws://127.0.0.1:8000/api/v1/ws"

passed = 0
failed = 0


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
                           json={"account": account, "device_name": "probe"})
    assert response.status_code == 200, response.text
    return response.json()


def headers(tokens: dict) -> dict:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


async def main() -> int:
    with httpx.Client(timeout=20) as client:
        alice = dev_login(client, "demo_alice")
        bob = dev_login(client, "demo_bob")
        alice_h, bob_h = headers(alice), headers(bob)
        alice_me = client.get(f"{BASE}/users/me", headers=alice_h).json()
        bob_me = client.get(f"{BASE}/users/me", headers=bob_h).json()
        check("dev login: two independent accounts", alice_me["id"] != bob_me["id"])

        chat = client.post(f"{BASE}/chats", headers=alice_h,
                           json={"type": "private", "member_ids": [bob_me["id"]]})
        if chat.status_code == 409:
            chat_id = next(c["id"] for c in client.get(f"{BASE}/chats", headers=alice_h).json()
                           if c["type"] == "private")
        else:
            assert chat.status_code == 201, chat.text
            chat_id = chat.json()["id"]

        # --- preview: what the row prints -------------------------------------
        sent = client.post(f"{BASE}/chats/{chat_id}/messages", headers=alice_h,
                           json={"text": "پیام فاز سه"})
        assert sent.status_code == 201, sent.text
        rows = client.get(f"{BASE}/chats", headers=alice_h).json()
        row = next(r for r in rows if r["id"] == chat_id)
        check("row: preview text", row["last_message"]["text"] == "پیام فاز سه")
        check("row: preview sender", row["last_message"]["sender_id"] == alice_me["id"])
        check("row: preview time", bool(row["last_message"]["created_at"]))
        check("row: unread counter is per user",
              row["unread_count"] == 0 and
              next(r for r in client.get(f"{BASE}/chats", headers=bob_h).json()
                   if r["id"] == chat_id)["unread_count"] >= 1)
        check("row: peer carries avatar hook and presence",
              "avatar_file_id" in row["peer"] and "is_online" in row["peer"])

        # --- realtime: message.new / presence.updated / event_id --------------
        async with websockets.connect(f"{WS}?token={bob['access_token']}") as socket:
            await socket.recv()  # ready
            await asyncio.sleep(0.3)
            client.post(f"{BASE}/chats/{chat_id}/messages", headers=alice_h,
                        json={"text": "زنده"})
            frames = []
            for _ in range(6):
                try:
                    frames.append(json.loads(await asyncio.wait_for(socket.recv(), timeout=3)))
                except asyncio.TimeoutError:
                    break
            types = [f["type"] for f in frames]
            check("ws: message.new reaches the peer", "message.new" in types, str(types))
            ids = [f.get("event_id") for f in frames]
            check("ws: every frame has a unique event_id",
                  bool(ids) and all(ids) and len(set(ids)) == len(ids))

        # --- read receipt ------------------------------------------------------
        bob_rows = client.get(f"{BASE}/chats", headers=bob_h).json()
        bob_row = next(r for r in bob_rows if r["id"] == chat_id)
        unread_before = bob_row["unread_count"]
        seq = bob_row["last_seq"]
        client.post(f"{BASE}/chats/{chat_id}/read", headers=bob_h, params={"seq": seq})
        after = next(r for r in client.get(f"{BASE}/chats", headers=bob_h).json()
                     if r["id"] == chat_id)
        check("read receipt clears the counter",
              unread_before >= 1 and after["unread_count"] == 0)

        # --- pin / unpin -------------------------------------------------------
        client.post(f"{BASE}/chats/{chat_id}/pin", headers=alice_h, json={"pinned": True})
        pinned_row = next(r for r in client.get(f"{BASE}/chats", headers=alice_h).json()
                          if r["id"] == chat_id)
        check("pin: dialog is pinned", pinned_row["pinned"] is True)
        client.post(f"{BASE}/chats/{chat_id}/pin", headers=alice_h, json={"pinned": False})
        check("pin: unpinned again",
              next(r for r in client.get(f"{BASE}/chats", headers=alice_h).json()
                   if r["id"] == chat_id)["pinned"] is False)

        # --- folders -----------------------------------------------------------
        folder = client.post(f"{BASE}/folders", headers=alice_h,
                             json={"name": "آزمایش فاز سه", "position": 0})
        if folder.status_code == 201:
            folder_id = folder.json()["id"]
            client.put(f"{BASE}/folders/{folder_id}/chats", headers=alice_h,
                       json={"chat_ids": [chat_id]})
            listed = client.get(f"{BASE}/folders", headers=alice_h).json()
            mine = next((f for f in listed if f["id"] == folder_id), None)
            check("folders: chat ids list the real dialogs",
                  mine is not None and mine["chat_ids"] == [chat_id])
        else:
            check("folders: create", False, folder.text)

        # --- archive -----------------------------------------------------------
        client.post(f"{BASE}/chats/{chat_id}/archive", headers=alice_h,
                    json={"archived": True})
        main_ids = {r["id"] for r in client.get(f"{BASE}/chats", headers=alice_h).json()}
        archived_ids = {r["id"] for r in client.get(
            f"{BASE}/chats", headers=alice_h, params={"archived": "true"}).json()}
        check("archive: leaves the main list, appears in the archive",
              chat_id not in main_ids and chat_id in archived_ids)
        client.post(f"{BASE}/chats/{chat_id}/archive", headers=alice_h,
                    json={"archived": False})
        check("archive: restored",
              chat_id in {r["id"] for r in client.get(f"{BASE}/chats", headers=alice_h).json()})

        # --- delete policy -----------------------------------------------------
        refused = client.delete(f"{BASE}/chats/{chat_id}", headers=alice_h)
        check("delete: private chat is refused (archive offered instead)",
              refused.status_code == 400)
        group = client.post(f"{BASE}/chats", headers=alice_h,
                            json={"type": "group", "title": "تیم فاز سه", "member_ids": []})
        if group.status_code == 201:
            gid = group.json()["id"]
            deleted = client.delete(f"{BASE}/chats/{gid}", headers=alice_h)
            gone = gid not in {r["id"] for r in client.get(f"{BASE}/chats", headers=alice_h).json()}
            check("delete: owned group is really removed", deleted.status_code == 204 and gone)

        # --- paging ------------------------------------------------------------
        page = client.get(f"{BASE}/chats", headers=alice_h, params={"limit": 1, "offset": 0}).json()
        check("paging: limit is honoured", len(page) <= 1)

    print(f"\n{passed} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
