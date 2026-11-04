#!/usr/bin/env python3
"""Development-login probe: two test accounts, real messages, real WebSocket.

Mirrors what the Android client does when you type a demo code (10001 / 10002)
into the phone field of the login screen, then exercises the messaging features
between the two accounts against a live server.

Usage:
    DEV_LOGIN_ENABLED=true uvicorn ... ; python tools/android_demo_login_probe.py
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
    print(f"  {PASS if condition else FAIL} {label}" + ("" if condition else f" {detail}"))
    if not condition:
        failures.append(label)


def section(title: str) -> None:
    print(f"\n\033[1m{title}\033[0m")


class Demo:
    """Same calls as PlatformAuthInterceptor.tryDevLogin + AuthManager."""

    def __init__(self, base: str, account: str, phone_code: str) -> None:
        self.base = base.rstrip("/")
        self.account = account
        self.phone_code = phone_code
        self.http = httpx.Client(base_url=self.base, timeout=15.0)
        self.access: str | None = None
        self.refresh: str | None = None

    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.access}"} if self.access else {}

    def dev_login(self) -> httpx.Response:
        response = self.http.post("/api/v1/auth/dev-login", json={
            "account": self.account, "device_name": "Probe emulator", "platform": "android",
        })
        if response.status_code == 200:
            pair = response.json()
            self.access, self.refresh = pair["access_token"], pair["refresh_token"]
        return response

    def me(self) -> dict:
        return self.http.get("/api/v1/users/me", headers=self.headers()).json()

    def get(self, path, **kwargs):
        return self.http.get(path, headers=self.headers(), **kwargs)

    def post(self, path, **kwargs):
        return self.http.post(path, headers=self.headers(), **kwargs)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--server", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    base = args.server

    alice = Demo(base, "demo_alice", "10001")
    bob = Demo(base, "demo_bob", "10002")

    section("1. development login (no SMS) for both accounts")
    check("alice signed in", alice.dev_login().status_code == 200)
    check("bob signed in", bob.dev_login().status_code == 200)
    alice_me, bob_me = alice.me(), bob.me()
    check("alice identity", alice_me.get("username") == "demo_alice", str(alice_me)[:120])
    check("bob identity", bob_me.get("username") == "demo_bob", str(bob_me)[:120])
    check("profiles are independent", alice_me.get("id") != bob_me.get("id"))
    check("display names differ",
          alice_me.get("display_name") != bob_me.get("display_name"),
          f"{alice_me.get('display_name')} / {bob_me.get('display_name')}")

    section("2. unknown demo account is refused")
    bogus = httpx.post(f"{base}/api/v1/auth/dev-login", json={"account": "demo_eve"})
    check("unknown account rejected", bogus.status_code == 404, str(bogus.status_code))
    real = httpx.post(f"{base}/api/v1/auth/dev-login", json={"account": "root"})
    check("a real account cannot use dev login", real.status_code == 404)

    section("3. session is a normal, revocable session")
    rotated = httpx.post(f"{base}/api/v1/auth/refresh", json={"refresh_token": alice.refresh})
    check("refresh works for a dev session", rotated.status_code == 200, rotated.text[:120])
    if rotated.status_code == 200:
        pair = rotated.json()
        alice.access, alice.refresh = pair["access_token"], pair["refresh_token"]
        check("rotated token authorises", alice.get("/api/v1/users/me").status_code == 200)
    sessions = alice.get("/api/v1/auth/sessions").json()
    check("session is listed with a device", any(s["current"] for s in sessions), str(sessions)[:150])

    section("4. two accounts exchange messages in real time")
    chat = alice.post("/api/v1/chats", json={"type": "private", "member_ids": [bob_me["id"]]})
    check("private chat created", chat.status_code == 201, chat.text[:120])
    chat_id = chat.json()["id"]

    try:
        from websockets.sync.client import connect as ws_connect
    except ImportError:
        print("  (websockets not installed: realtime checks skipped)")
        return 0

    with ws_connect(f"{base.replace('http', 'ws')}/api/v1/ws?token={bob.access}",
                    open_timeout=10) as socket:
        hello = json.loads(socket.recv(timeout=10))
        check("bob's socket is ready", hello.get("type") == "ready")

        rows = alice.get("/api/v1/chats").json()
        row = next(c for c in rows if c["id"] == chat_id)
        check("bob shows as online", row["peer"]["is_online"] is True, str(row["peer"])[:120])

        sent = alice.post(f"/api/v1/chats/{chat_id}/messages",
                          json={"type": "text", "text": f"hello from alice {uuid.uuid4().hex[:6]}",
                                "client_message_id": "demo-a1"})
        check("alice can send", sent.status_code == 201, sent.text[:120])
        text = sent.json()["text"]

        deadline, event = time.time() + 10, None
        while time.time() < deadline and event is None:
            frame = json.loads(socket.recv(timeout=5))
            if frame.get("type") == "message.new" and frame["data"].get("text") == text:
                event = frame
        check("bob receives it over WebSocket", event is not None)
        if event:
            check("event carries chat and sender",
                  event["data"]["chat_id"] == chat_id
                  and event["data"]["sender_id"] == alice_me["id"], str(event["data"])[:160])

    rows = bob.get("/api/v1/chats").json()
    row = next(c for c in rows if c["id"] == chat_id)
    check("bob shows alice as offline after the socket closed", row["peer"]["is_online"] is False)
    check("unread counter counts alice's message", row["unread_count"] == 1, str(row["unread_count"]))

    reply = bob.post(f"/api/v1/chats/{chat_id}/messages",
                     json={"type": "text", "text": "reply from bob"})
    check("bob replies", reply.status_code == 201, reply.text[:120])

    history = alice.get(f"/api/v1/chats/{chat_id}/messages").json()
    texts = [item["text"] for item in history["items"]]
    check("history shows both directions", len(texts) >= 2 and texts[-1] == "reply from bob", str(texts))

    section("5. read receipts")
    rows = alice.get("/api/v1/chats").json()
    row = next(c for c in rows if c["id"] == chat_id)
    check("alice sees an unread reply", row["unread_count"] == 1, str(row["unread_count"]))
    read = alice.post(f"/api/v1/chats/{chat_id}/read", params={"seq": row["last_seq"]})
    check("read receipt accepted", read.status_code in (200, 204), str(read.status_code))
    rows = alice.get("/api/v1/chats").json()
    check("counter cleared after reading",
          next(c for c in rows if c["id"] == chat_id)["unread_count"] == 0)

    section("6. session restore after an app restart")
    restored = Demo(base, "demo_alice", "10001")
    restored.access, restored.refresh = alice.access, alice.refresh
    response = httpx.post(f"{base}/api/v1/auth/refresh", json={"refresh_token": restored.refresh})
    if response.status_code == 200:
        pair = response.json()
        restored.access, restored.refresh = pair["access_token"], pair["refresh_token"]
    check("restored session reaches /users/me", restored.get("/api/v1/users/me").status_code == 200)
    check("history survives the restart",
          any(item["text"] == "reply from bob"
              for item in restored.get(f"/api/v1/chats/{chat_id}/messages").json()["items"]))

    print()
    if failures:
        print(f"\033[31m{len(failures)} checks failed\033[0m")
        return 1
    print("\033[32mall development-login checks passed\033[0m")
    return 0


if __name__ == "__main__":
    sys.exit(main())
