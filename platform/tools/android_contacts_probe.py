"""Phase 5 acceptance probe — contacts, profiles, privacy, global search.

Runs against a live server with DEV_LOGIN_ENABLED=true:

    python tools/android_contacts_probe.py

Two demo accounts only; API level, not an app test.
"""
from __future__ import annotations

import asyncio
import json
import sys

import httpx
import websockets

BASE = "http://127.0.0.1:8000/api/v1"
WS = "ws://127.0.0.1:8000/api/v1/ws"

passed = failed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  ok   {name}")
    else:
        failed += 1
        print(f"  FAIL {name} {detail}")


def h(tokens: dict) -> dict:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


def dev_login(client: httpx.Client, account: str) -> dict:
    response = client.post(f"{BASE}/auth/dev-login",
                           json={"account": account, "device_name": "contacts-probe"})
    assert response.status_code == 200, response.text
    return response.json()


async def main() -> int:
    with httpx.Client(timeout=20) as client:
        alice = dev_login(client, "demo_alice")
        bob = dev_login(client, "demo_bob")
        a, b = h(alice), h(bob)
        alice_me = client.get(f"{BASE}/users/me", headers=a).json()
        bob_me = client.get(f"{BASE}/users/me", headers=b).json()

        # ---- contacts -----------------------------------------------------
        client.delete(f"{BASE}/contacts/{bob_me['id']}", headers=a)
        added = client.post(f"{BASE}/contacts", headers=a,
                            json={"contact_id": bob_me["id"], "first_name": "باب"})
        check("add contact", added.status_code == 201, added.text)
        contacts = client.get(f"{BASE}/contacts", headers=a).json()
        check("contact list contains the account",
              any(str(c.get("contact_id")) == bob_me["id"] for c in contacts), str(contacts)[:120])

        # ---- profile ------------------------------------------------------
        profile = client.get(f"{BASE}/users/{bob_me['id']}", headers=a).json()
        check("profile is readable", profile.get("username") == bob_me["username"])
        check("profile exposes no private fields",
              not ({"settings", "search_privacy", "password_hash"} & set(profile)))

        # ---- presence privacy --------------------------------------------
        client.patch(f"{BASE}/users/me/privacy", headers=b, json={"last_seen_privacy": "nobody"})
        hidden = client.get(f"{BASE}/users/{bob_me['id']}", headers=a).json()
        check("presence hidden when privacy is nobody",
              hidden["presence_visible"] is False and hidden["is_online"] is False)
        client.patch(f"{BASE}/users/me/privacy", headers=b, json={"last_seen_privacy": "contacts"})
        friend = client.get(f"{BASE}/users/{bob_me['id']}", headers=a).json()
        check("contact sees presence again", friend["presence_visible"] is True)

        # ---- search privacy ----------------------------------------------
        client.patch(f"{BASE}/users/me/privacy", headers=b, json={"search_privacy": "nobody"})
        check("search honours search_privacy=nobody",
              client.get(f"{BASE}/users/search", headers=a,
                         params={"q": bob_me["username"]}).json() == [])
        client.patch(f"{BASE}/users/me/privacy", headers=b, json={"search_privacy": "everybody"})
        found = client.get(f"{BASE}/users/search", headers=a,
                           params={"q": bob_me["username"]}).json()
        check("search finds the account again",
              any(u["id"] == bob_me["id"] for u in found))

        # ---- global search ------------------------------------------------
        result = client.get(f"{BASE}/search", headers=a,
                            params={"q": bob_me["username"]}).json()
        check("global search returns users and chats",
              any(u["id"] == bob_me["id"] for u in result["users"])
              and isinstance(result["chats"], list))
        stranger_result = client.get(f"{BASE}/search", headers=b, params={"q": "demo"}).json()
        check("global search never leaks chats the viewer is not in",
              all(c["id"] != "00000000-0000-0000-0000-000000000000"
                  for c in stranger_result["chats"]))

        # ---- profile update over the socket -------------------------------
        bob_id = bob_me["id"]
        chat = client.post(f"{BASE}/chats", headers=a,
                            json={"type": "private", "member_ids": [bob_id]})
        async with websockets.connect(f"{WS}?token={alice['access_token']}") as socket:
            await socket.recv()
            updated = client.patch(f"{BASE}/users/me", headers=b,
                                   json={"display_name": "Bob Demo Updated"})
            check("profile edit accepted", updated.status_code == 200, updated.text)
            frame = None
            for _ in range(8):
                candidate = json.loads(await asyncio.wait_for(socket.recv(), timeout=4))
                if candidate.get("type") == "profile.updated":
                    frame = candidate
                    break
            check("profile.updated reaches the other account",
                  frame is not None and frame["data"]["display_name"] == "Bob Demo Updated")
        # restore the demo profile
        client.patch(f"{BASE}/users/me", headers=b, json={"display_name": "Bob Demo"})

        # ---- block / unblock ---------------------------------------------
        assert client.post(f"{BASE}/users/{bob_id}/block", headers=a).status_code == 204
        blocked = client.get(f"{BASE}/users/{bob_id}", headers=a).json()
        check("blocked account hides presence",
              blocked["presence_visible"] is False)
        assert client.delete(f"{BASE}/users/{bob_id}/block", headers=a).status_code == 204
        check("unblock restores visibility",
              client.get(f"{BASE}/users/{bob_id}", headers=a).json()["presence_visible"] is True)

        # ---- profile edit of own name/bio ---------------------------------
        me = client.patch(f"{BASE}/users/me", headers=a,
                          json={"display_name": "Alice Demo", "bio": "پروفایل آزمایشی"}).json()
        check("own profile edit round-trips",
              me["display_name"] == "Alice Demo" and me["bio"] == "پروفایل آزمایشی")

    print(f"\n{passed} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
