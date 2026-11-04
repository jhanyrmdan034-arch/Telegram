#!/usr/bin/env python3
"""Replays the exact call sequence of the Android platform client against a live server.

The Java module in ``TMessagesProj/src/main/java/org/telegram/platform`` cannot be
compiled in this sandbox (no JDK/Android SDK), so this probe verifies the other half
of the contract: that the requests the client sends, and the field names it reads,
are really produced by the running backend — including a live WebSocket push.

Usage:
    python tools/android_contract_probe.py --server http://127.0.0.1:8000
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import uuid

import httpx

PASS, FAIL = "\033[32m✔\033[0m", "\033[31m✘\033[0m"
failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  {PASS} {label}")
    else:
        print(f"  {FAIL} {label} {detail}")
        failures.append(label)


def section(title: str) -> None:
    print(f"\n\033[1m{title}\033[0m")


class Client:
    """Mirrors PlatformApi: bearer token plus the same JSON bodies."""

    def __init__(self, base: str) -> None:
        self.http = httpx.Client(base_url=base, timeout=20.0)
        self.access: str | None = None
        self.refresh: str | None = None

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.access}"} if self.access else {}

    def call(self, method: str, path: str, body: dict | None = None, params: dict | None = None):
        return self.http.request(method, path, json=body, params=params, headers=self._headers())

    def adopt(self, pair: dict) -> None:
        self.access, self.refresh = pair["access_token"], pair["refresh_token"]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--server", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    base = args.server.rstrip("/")
    suffix = uuid.uuid4().hex[:8]

    alice, bob = Client(base), Client(base)
    alice_username, bob_username = f"probe_a_{suffix}", f"probe_b_{suffix}"
    password = "ProbePass123"

    section("0. bootstrap config (no auth) — GET /api/v1/config")
    config = httpx.get(f"{base}/api/v1/config", timeout=10)
    check("config returns 200", config.status_code == 200, config.text[:120])
    cfg = config.json()
    for key in ("registration_enabled", "calls_enabled", "max_file_size_bytes", "maintenance"):
        check(f"config exposes {key}", key in cfg)

    section("1. registration — POST /api/v1/auth/register")
    registered = alice.call("POST", "/api/v1/auth/register", {
        "username": alice_username, "password": password,
        "display_name": "Probe Alice", "device_name": "Pixel 7", "platform": "android",
    })
    check("alice registered", registered.status_code == 201, registered.text[:160])
    alice.adopt(registered.json())
    check("token pair has access + refresh",
          bool(alice.access) and bool(alice.refresh))
    me = alice.call("GET", "/api/v1/users/me").json()
    check("me.id present", "id" in me)
    check("me.display_name matches", me.get("display_name") == "Probe Alice")

    bob.call("POST", "/api/v1/auth/register", {
        "username": bob_username, "password": password,
        "display_name": "Probe Bob", "device_name": "Galaxy S23", "platform": "android",
    }).raise_for_status()
    bob.adopt(bob.call("POST", "/api/v1/auth/login",
                       {"username": bob_username, "password": password,
                        "device_name": "Galaxy S23", "platform": "android"}).json())

    section("2. OTP sign-in — POST /auth/otp/request + /auth/otp/verify")
    challenge = bob.call("POST", "/api/v1/auth/otp/request",
                         {"username": bob_username, "purpose": "login"})
    check("otp request accepted", challenge.status_code == 202, challenge.text[:160])
    body = challenge.json()
    check("otp response carries delivery + expires_in",
          "delivery" in body and "expires_in" in body)
    code = body.get("debug_code")
    if code:
        verified = bob.call("POST", "/api/v1/auth/otp/verify", {
            "username": bob_username, "code": code,
            "device_name": "Galaxy S23", "platform": "android"})
        check("otp verify returns tokens", verified.status_code == 200, verified.text[:160])
        if verified.status_code == 200:
            bob.adopt(verified.json())
    else:
        print("  (otp_debug disabled on this server: code is in the SMS/log sink)")

    section("3. token refresh — POST /api/v1/auth/refresh")
    pair = alice.call("POST", "/api/v1/auth/refresh",
                      {"refresh_token": alice.refresh})
    check("refresh returns a new pair", pair.status_code == 200, pair.text[:160])
    if pair.status_code == 200:
        alice.adopt(pair.json())
        check("me still reachable after refresh",
              alice.call("GET", "/api/v1/users/me").status_code == 200)

    section("4. open private dialog — POST /api/v1/chats")
    bob_id = bob.call("GET", "/api/v1/users/me").json()["id"]
    chat = alice.call("POST", "/api/v1/chats",
                      {"type": "private", "member_ids": [bob_id]})
    check("private chat created", chat.status_code == 201, chat.text[:160])
    chat_id = chat.json()["id"]

    section("5. dialog list fields the UI reads — GET /api/v1/chats")
    listing = bob.call("GET", "/api/v1/chats").json()
    row = next((c for c in listing if c["id"] == chat_id), None)
    check("chat appears in bob's list", row is not None)
    if row:
        for key in ("last_message", "unread_count", "pinned", "peer", "last_seq"):
            check(f"row exposes {key}", key in row)
        peer = row.get("peer") or {}
        for key in ("id", "display_name", "username", "is_online"):
            check(f"peer exposes {key}", key in peer)

    section("6. realtime before sending — WebSocket /api/v1/ws")
    ws_event = None
    try:
        from websockets.sync.client import connect as ws_connect
        with ws_connect(f"{base.replace('http', 'ws')}/api/v1/ws?token={bob.access}",
                        open_timeout=10) as socket:
            hello = json.loads(socket.recv(timeout=10))
            check("socket greets with ready", hello.get("type") == "ready", str(hello)[:120])
            check("peer is online for alice",
                  any((c.get("peer") or {}).get("is_online")
                      for c in alice.call("GET", "/api/v1/chats").json() if c["id"] == chat_id))

            sent = alice.call("POST", f"/api/v1/chats/{chat_id}/messages", {
                "type": "text", "text": "hello from the probe",
                "client_message_id": "probe-" + suffix})
            check("send accepted", sent.status_code == 201, sent.text[:160])
            message = sent.json()
            check("client_message_id echoed in meta",
                  message.get("meta", {}).get("client_message_id") == "probe-" + suffix)

            deadline = time.time() + 10
            while time.time() < deadline and ws_event is None:
                event = json.loads(socket.recv(timeout=5))
                if event.get("type") == "message.new":
                    ws_event = event
            check("bob received message.new over WebSocket", ws_event is not None,
                  str(ws_event)[:200])
            if ws_event:
                data = ws_event["data"]
                check("event data carries id/chat_id/text",
                      {"id", "chat_id", "text"} <= set(data))
                check("event text matches", data.get("text") == "hello from the probe")
    except ImportError:
        print("  (websockets not installed: WebSocket check skipped)")
    except Exception as exc:  # noqa: BLE001
        check("websocket conversation", False, repr(exc)[:200])

    section("7. history page — GET /api/v1/chats/{id}/messages")
    page = bob.call("GET", f"/api/v1/chats/{chat_id}/messages", params={"limit": 20})
    check("page returns 200", page.status_code == 200, page.text[:160])
    payload = page.json()
    check("page has items + next_before_seq",
          "items" in payload and "next_before_seq" in payload)
    check("history contains the message",
          any("hello from the probe" == m.get("text") for m in payload.get("items", [])))
    if payload.get("items"):
        item = payload["items"][-1]
        for key in ("id", "chat_id", "sender_id", "seq", "text", "created_at", "meta"):
            check(f"message exposes {key}", key in item)

    section("8. unread counter + read receipt")
    row = next(c for c in bob.call("GET", "/api/v1/chats").json() if c["id"] == chat_id)
    check("unread_count is 1 for bob", row["unread_count"] == 1, str(row["unread_count"]))
    read = bob.call("POST", f"/api/v1/chats/{chat_id}/read", params={"seq": row["last_seq"]})
    check("read receipt accepted", read.status_code in (200, 204), read.text[:160])
    row = next(c for c in bob.call("GET", "/api/v1/chats").json() if c["id"] == chat_id)
    check("unread_count cleared", row["unread_count"] == 0, str(row["unread_count"]))

    section("9. pinning + folders")
    pin = alice.call("POST", f"/api/v1/chats/{chat_id}/pin", {"pinned": True})
    check("pin accepted", pin.status_code == 204, pin.text[:160])
    pinned = alice.call("GET", "/api/v1/chats/pinned").json()
    check("pinned list contains the chat", any(c["id"] == chat_id for c in pinned))
    check("dialog list puts pinned first",
          alice.call("GET", "/api/v1/chats").json()[0]["id"] == chat_id)

    folder = alice.call("POST", "/api/v1/folders", {"name": f"Probe {suffix}", "position": 1})
    check("folder created", folder.status_code == 201, folder.text[:160])
    folder_id = folder.json()["id"]
    assigned = alice.call("PUT", f"/api/v1/folders/{folder_id}/chats",
                          {"chat_ids": [chat_id]})
    check("folder assignment accepted", assigned.status_code == 200, assigned.text[:160])
    folder_chats = alice.call("GET", f"/api/v1/folders/{folder_id}/chats").json()
    check("folder returns its chats", [c["id"] for c in folder_chats] == [chat_id])
    alice.call("DELETE", f"/api/v1/folders/{folder_id}")

    section("10. forward + search")
    forwarded = alice.call("POST", f"/api/v1/messages/{message['id']}/forward",
                           {"chat_ids": [chat_id]})
    check("forward accepted", forwarded.status_code == 201, forwarded.text[:160])
    if forwarded.status_code == 201:
        copy = forwarded.json()[0]
        check("forward carries forwarded_from meta",
              copy.get("meta", {}).get("forwarded_from", {}).get("message_id") == message["id"])
    found = alice.call("GET", f"/api/v1/chats/{chat_id}/messages/search",
                       params={"q": "hello from the probe"})
    check("search returns matches", found.status_code == 200 and len(found.json()) >= 1,
          found.text[:160])

    section("11. logout invalidates the session")
    logout = bob.call("POST", "/api/v1/auth/logout", {})
    check("logout accepted", logout.status_code in (200, 204), logout.text[:160])
    after = bob.call("GET", "/api/v1/users/me")
    check("token rejected after logout", after.status_code == 401, str(after.status_code))

    print()
    if failures:
        print(f"\033[31m{len(failures)} checks failed\033[0m")
        return 1
    print("\033[32mall contract checks passed\033[0m")
    return 0


if __name__ == "__main__":
    sys.exit(main())
