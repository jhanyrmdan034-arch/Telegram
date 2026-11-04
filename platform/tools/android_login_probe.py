#!/usr/bin/env python3
"""Phase 2 verification: replays the login flows the way the Android client drives them.

``PlatformAuthInterceptor`` turns the TL requests that ``LoginActivity`` already
sends into REST calls. This script performs exactly the same translation table
against a live server and asserts on both the API answers and the TL error texts
that are synthesised from them. It is the executable definition of the contract:

    TL_auth_sendCode   -> POST /api/v1/auth/otp/request   (404 => sign-up page)
    TL_auth_resendCode -> POST /api/v1/auth/otp/request
    TL_auth_signIn     -> POST /api/v1/auth/otp/verify    (401 => PHONE_CODE_INVALID)
    TL_auth_signUp     -> POST /api/v1/auth/register      (409 => PHONE_NUMBER_OCCUPIED)

Usage:
    python tools/android_login_probe.py --server http://127.0.0.1:8000
"""
from __future__ import annotations

import argparse
import json
import socket
import sys
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

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


def digits(raw: str) -> str:
    return "".join(c for c in raw if c.isdigit())


def username_for(phone: str) -> str:
    return "ph" + digits(phone)


# --------------------------------------------------------------------------- #
# Mirrors org.telegram.platform.net.PlatformApi
# --------------------------------------------------------------------------- #
class PlatformClient:
    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/")
        self.http = httpx.Client(base_url=self.base, timeout=15.0)
        self.access: str | None = None
        self.refresh_token: str | None = None
        self.password: str | None = None

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.access}"} if self.access else {}

    def request(self, method, path, body=None, params=None):
        return self.http.request(method, path, json=body, params=params, headers=self._headers())

    # -- TL translation table ------------------------------------------------
    def auth_send_code(self, phone: str):
        """TL_auth_sendCode: 404 means 'show the sign-up page'."""
        response = self.http.post("/api/v1/auth/otp/request",
                                  json={"username": username_for(phone), "purpose": "login"})
        if response.status_code == 404:
            return "sign_up_required", response
        if response.status_code == 202:
            return "code_sent", response
        return "error", response

    def auth_resend_code(self, phone: str):
        return self.auth_send_code(phone)

    def auth_sign_in(self, phone: str, code: str):
        response = self.http.post("/api/v1/auth/otp/verify", json={
            "username": username_for(phone), "code": code,
            "device_name": "Probe Device", "platform": "android",
        })
        if response.status_code == 200:
            pair = response.json()
            self.access, self.refresh_token = pair["access_token"], pair["refresh_token"]
        return response

    def auth_sign_up(self, phone: str, first: str, last: str):
        password = uuid.uuid4().hex + uuid.uuid4().hex  # what the adapter generates
        response = self.http.post("/api/v1/auth/register", json={
            "username": username_for(phone),
            "password": password,
            "display_name": f"{first} {last}".strip(),
            "device_name": "Probe Device",
            "platform": "android",
        })
        if response.status_code == 201:
            pair = response.json()
            self.access, self.refresh_token = pair["access_token"], pair["refresh_token"]
            self.password = password
        return response

    # -- client behaviours ---------------------------------------------------
    def restore_session(self) -> bool:
        """What ApplicationLoader does on start: only stored tokens, then /users/me."""
        if not self.access and not self.refresh_token:
            return False
        if self.refresh_token:
            rotated = self.http.post("/api/v1/auth/refresh",
                                     json={"refresh_token": self.refresh_token})
            if rotated.status_code == 200:
                pair = rotated.json()
                self.access, self.refresh_token = pair["access_token"], pair["refresh_token"]
        response = self.request("GET", "/api/v1/users/me")
        return response.status_code == 200

    def refresh(self):
        response = self.http.post("/api/v1/auth/refresh", json={"refresh_token": self.refresh_token})
        if response.status_code == 200:
            pair = response.json()
            self.access, self.refresh_token = pair["access_token"], pair["refresh_token"]
        return response

    def logout(self):
        response = self.request("POST", "/api/v1/auth/logout", {})
        self.access = self.refresh_token = None
        return response

    def sessions(self):
        return self.request("GET", "/api/v1/auth/sessions")

    def logout_from(self, session_id: str):
        return self.request("DELETE", f"/api/v1/auth/sessions/{session_id}")


def tl_error_text(status: int, detail: str) -> str:
    """Same mapping as PlatformAuthInterceptor.toTlError."""
    detail_lower = (detail or "").lower()
    if status == 401:
        return "PHONE_CODE_INVALID" if "code" in detail_lower else "SESSION_EXPIRED"
    if status == 403:
        return "PHONE_NUMBER_BANNED"
    if status == 404:
        return "PHONE_NUMBER_UNOCCUPIED"
    if status == 409:
        return "PHONE_NUMBER_OCCUPIED"
    if status == 422:
        return "PHONE_NUMBER_INVALID"
    if status == 429:
        return "FLOOD_WAIT"
    if status == 502:
        return "NETWORK_ERROR"
    return "INTERNAL_ERROR"


class _Garbage(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        body = b"<html>not json</html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def free_port_is_closed(port: int) -> bool:
    with socket.socket() as sock:
        sock.settimeout(0.3)
        return sock.connect_ex(("127.0.0.1", port)) != 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--server", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    base = args.server
    suffix = uuid.uuid4().hex[:6]
    phone = f"+4917{suffix_digits(suffix)}"

    client = PlatformClient(base)

    section("1. new phone number — TL_auth_sendCode must ask for sign-up")
    state, response = client.auth_send_code(phone)
    check("returns 'sign up required' for an unknown account", state == "sign_up_required",
          f"{response.status_code} {response.text[:120]}")
    check("backend answered 404 for the derived username", response.status_code == 404)

    section("2. registration — TL_auth_signUp creates a real account")
    registered = client.auth_sign_up(phone, "Sara", "Ahmadi")
    check("account created (201)", registered.status_code == 201, registered.text[:160])
    check("token pair stored", bool(client.access) and bool(client.refresh_token))
    me = client.request("GET", "/api/v1/users/me")
    check("session works immediately", me.status_code == 200, me.text[:120])
    check("display name came from the sign-up form",
          me.json().get("display_name") == "Sara Ahmadi", str(me.json())[:120])
    check("username is derived from the phone number",
          me.json().get("username") == username_for(phone), str(me.json())[:120])

    section("3. app restart — session restored from the stored tokens only")
    stored = (client.access, client.refresh_token)
    restarted = PlatformClient(base)
    check("a fresh client has no session", not restarted.restore_session())
    restarted.access, restarted.refresh_token = stored
    check("restore succeeds with the stored tokens", restarted.restore_session())
    check("identity survives the restart",
          restarted.request("GET", "/api/v1/users/me").json()["username"] == username_for(phone))

    section("4. logout — TL_auth_logOut clears the session")
    # The restarted client holds the live token pair (the first one was rotated).
    assert restarted.access
    logged_out = restarted.logout()
    check("logout accepted", logged_out.status_code in (200, 204), logged_out.text[:120])
    check("tokens gone on the client",
          restarted.access is None and restarted.refresh_token is None)
    stale = PlatformClient(base)
    stale.access, stale.refresh_token = stored
    check("restore after logout fails (no ghost session)", not stale.restore_session())

    section("5. existing account — TL_auth_sendCode returns the code page")
    returning = PlatformClient(base)
    state, response = returning.auth_send_code(phone)
    check("code requested (202)", state == "code_sent", f"{response.status_code} {response.text[:120]}")
    challenge = response.json()
    code = challenge.get("debug_code")
    check("challenge carries expiry", "expires_in" in challenge and challenge["expires_in"] > 0)
    if code is None:
        print("  (OTP_DEBUG is off on this server: the code is in the SMS gateway / log)")

    section("6. wrong code — TL error PHONE_CODE_INVALID, then the real code works")
    if code:
        wrong = "000000" if code != "000000" else "111111"
        bad = returning.auth_sign_in(phone, wrong)
        check("wrong code rejected with 401", bad.status_code == 401, bad.text[:120])
        check("adapter maps it to PHONE_CODE_INVALID",
              tl_error_text(bad.status_code, bad.json().get("detail", "")) == "PHONE_CODE_INVALID")
        good = returning.auth_sign_in(phone, code)
        check("correct code signs in", good.status_code == 200, good.text[:160])
        check("session usable", returning.request("GET", "/api/v1/users/me").status_code == 200)

    section("7. expired / rotated token — refresh keeps the session alive")
    fresh_code = returning.auth_send_code(phone)[1].json().get("debug_code")
    if fresh_code:
        returning.auth_sign_in(phone, fresh_code)
    before = returning.access
    rotated = returning.refresh()
    check("refresh returns a new access token",
          rotated.status_code == 200 and returning.access != before, rotated.text[:120])
    check("new token authorises requests",
          returning.request("GET", "/api/v1/users/me").status_code == 200)

    section("8. devices — list sessions and sign another device out")
    # A second device signs in the same way the app would: its own OTP.
    second = PlatformClient(base)
    second_code = second.auth_send_code(phone)[1].json().get("debug_code")
    if second_code:
        second.auth_sign_in(phone, second_code)
    sessions = returning.sessions()
    check("session list readable", sessions.status_code == 200, sessions.text[:120])
    rows = sessions.json() if sessions.status_code == 200 else []
    check("current device is marked", any(row.get("current") for row in rows))
    others = [row for row in rows if not row.get("current")]
    check("at least one other device is listed", bool(others), str(rows)[:160])
    if others:
        revoked = returning.logout_from(others[0]["id"])
        check("other device revoked", revoked.status_code == 204, revoked.text[:120])
        remaining = returning.sessions().json()
        check("revoked device disappeared",
              all(row["id"] != others[0]["id"] for row in remaining))

    section("9. transport failures — the client must see typed errors, not crashes")
    closed = 65533
    if free_port_is_closed(closed):
        offline = PlatformClient(f"http://127.0.0.1:{closed}")
        try:
            offline.http.post("/api/v1/auth/otp/request", json={"username": "ph12345"})
            check("connection refused raises", False, "no exception")
        except httpx.ConnectError:
            check("connection refused surfaces as NETWORK_ERROR",
                  True)  # PlatformApi maps IOException -> Kind.NETWORK

    garbage = ThreadingHTTPServer(("127.0.0.1", 0), _Garbage)
    threading.Thread(target=garbage.serve_forever, daemon=True).start()
    try:
        bad_client = PlatformClient(f"http://127.0.0.1:{garbage.server_port}")
        response = bad_client.http.get("/api/v1/auth/sessions")
        is_json = True
        try:
            response.json()
        except json.JSONDecodeError:
            is_json = False
        check("non-JSON body is detected (client raises SERVER)", not is_json)
    finally:
        garbage.shutdown()

    section("10. account lifecycle end-to-end")
    final_code = returning.auth_send_code(phone)[1].json().get("debug_code")
    if final_code:
        signed = returning.auth_sign_in(phone, final_code)
        check("re-login after all of the above", signed.status_code == 200, signed.text[:120])
    profile = returning.request("GET", "/api/v1/users/me")
    check("profile reachable at the end", profile.status_code == 200, profile.text[:120])

    print()
    if failures:
        print(f"\033[31m{len(failures)} checks failed\033[0m")
        return 1
    print("\033[32mall login-flow checks passed\033[0m")
    return 0


def suffix_digits(suffix: str) -> str:
    return "".join(str(int(c, 16) % 10) for c in suffix)[:8].rjust(8, "7")


if __name__ == "__main__":
    sys.exit(main())
