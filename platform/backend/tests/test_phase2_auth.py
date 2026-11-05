"""Phase 2 — authentication surface used by the Android login screens.

These tests cover the parts the client depends on beyond the plain happy path:
code delivery through a real HTTP gateway, delivery failures, access-token
expiry with refresh, and device (session) management.
"""
from __future__ import annotations

import json
import threading
import uuid
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import jwt
import pytest

from app.config import Settings
from app.notifications import (
    DeliveryError,
    KavenegarSmsSender,
    TwilioSmsSender,
    WebhookSmsSender,
    build_sms_sender,
)
from tests.conftest import auth, register_user

# --------------------------------------------------------------------------- #
# Real HTTP gateways, exercised over a real socket
# --------------------------------------------------------------------------- #
class _Recorder(BaseHTTPRequestHandler):
    requests: list[dict] = []
    status = 201

    def _record(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length).decode() if length else ""
        type(self).requests.append({
            "method": self.command,
            "path": self.path,
            "headers": dict(self.headers),
            "body": body,
        })
        self.send_response(201 if "Messages.json" in self.path else self.status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"ok": True}).encode())

    def do_POST(self):  # noqa: N802 - http.server API
        self._record()

    def do_GET(self):  # noqa: N802 - http.server API
        self._record()

    def log_message(self, *args):  # silence the test output
        pass


@pytest.fixture()
def gateway():
    _Recorder.requests = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Recorder)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}", _Recorder.requests
    server.shutdown()


async def test_twilio_sender_sends_form_encoded_message(gateway):
    base_url, requests = gateway
    sender = TwilioSmsSender("AC123", "secret-token", "+15550001111", base_url=base_url)
    assert await sender.send("+4915112345678", "code 123456")

    assert len(requests) == 1
    request = requests[0]
    assert request["path"] == "/2010-04-01/Accounts/AC123/Messages.json"
    assert "To=%2B4915112345678" in request["body"]
    assert "Body=code+123456" in request["body"]
    assert request["headers"]["Authorization"].startswith("Basic ")


async def test_kavenegar_sender_uses_query_parameters(gateway):
    base_url, requests = gateway
    sender = KavenegarSmsSender("api-key-1", "10004346", base_url=base_url)
    assert await sender.send("09151234567", "code 654321")

    assert len(requests) == 1
    path = requests[0]["path"]
    assert path.startswith("/v1/api-key-1/sms/send.json?")
    assert "receptor=09151234567" in path
    assert "sender=10004346" in path


async def test_webhook_sender_posts_json_with_bearer(gateway):
    base_url, requests = gateway
    sender = WebhookSmsSender(base_url + "/hook", token="tok", from_number="MSG")
    assert await sender.send("09151234567", "code 111222")

    request = requests[0]
    assert request["path"] == "/hook"
    assert request["headers"]["Authorization"] == "Bearer tok"
    payload = json.loads(request["body"])
    assert payload == {"to": "09151234567", "from": "MSG", "text": "code 111222"}


async def test_gateway_rejection_is_reported_as_delivery_error(gateway):
    base_url, _ = gateway
    _Recorder.status = 500
    try:
        sender = WebhookSmsSender(base_url + "/hook")
        with pytest.raises(DeliveryError):
            await sender.send("09150000000", "x")
    finally:
        _Recorder.status = 201


def test_gateway_selection_requires_credentials():
    with pytest.raises(ValueError):
        build_sms_sender(Settings(sms_provider="twilio"))
    with pytest.raises(ValueError):
        build_sms_sender(Settings(sms_provider="kavenegar"))
    with pytest.raises(ValueError):
        build_sms_sender(Settings(sms_provider="webhook"))
    with pytest.raises(ValueError):
        build_sms_sender(Settings(sms_provider="nonsense"))

    sender = build_sms_sender(Settings(
        sms_provider="twilio",
        twilio_account_sid="AC1", twilio_auth_token="t", twilio_from_number="+1",
    ))
    assert sender.name == "twilio"
    assert build_sms_sender(Settings(sms_provider="log")).name == "log"


def test_production_rejects_log_sender():
    settings = Settings(
        environment="production",
        jwt_secret="x" * 40,
        database_url="postgresql+asyncpg://u:p@h/db",
        sms_provider="log",
        otp_debug=False,
    )
    with pytest.raises(RuntimeError) as exc:
        settings.validate_for_production()
    assert "SMS_PROVIDER" in str(exc.value)


# --------------------------------------------------------------------------- #
# OTP request behaves correctly when the gateway is down
# --------------------------------------------------------------------------- #
async def test_otp_request_returns_502_when_delivery_fails(client, alice, monkeypatch):
    from app.routers import auth as auth_router

    class BrokenSender:
        name = "twilio"

        async def send(self, destination, text):
            raise DeliveryError("gateway down")

    monkeypatch.setattr(auth_router, "build_sms_sender", lambda settings: BrokenSender())

    failed = await client.post("/api/v1/auth/otp/request", json={"username": alice["username"]})
    assert failed.status_code == 502
    assert "delivered" in failed.json()["detail"].lower()

    # The failed attempt must not leave a usable code behind.
    monkeypatch.undo()
    fresh = await client.post("/api/v1/auth/otp/request", json={"username": alice["username"]})
    assert fresh.status_code == 202
    verified = await client.post("/api/v1/auth/otp/verify", json={
        "username": alice["username"], "code": fresh.json()["debug_code"],
    })
    assert verified.status_code == 200


# --------------------------------------------------------------------------- #
# Access-token expiry and refresh (the client refreshes automatically)
# --------------------------------------------------------------------------- #
async def test_expired_access_token_is_rejected_then_refresh_recovers(client, alice, settings):
    me = await client.get("/api/v1/users/me", headers=auth(alice["access_token"]))
    assert me.status_code == 200

    claims = jwt.decode(alice["access_token"], settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    stale = jwt.encode(
        {
            "sub": claims["sub"],
            "sid": claims["sid"],
            "scopes": claims.get("scopes", []),
            "iat": datetime.now(UTC) - timedelta(hours=2),
            "exp": datetime.now(UTC) - timedelta(hours=1),
            "type": "access",
        },
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )
    rejected = await client.get("/api/v1/users/me", headers=auth(stale))
    assert rejected.status_code == 401

    rotated = await client.post("/api/v1/auth/refresh", json={"refresh_token": alice["refresh_token"]})
    assert rotated.status_code == 200
    pair = rotated.json()
    assert pair["access_token"] != stale
    assert (await client.get("/api/v1/users/me", headers=auth(pair["access_token"]))).status_code == 200


async def test_refresh_token_reuse_revokes_the_session_family(client, alice):
    """Replaying a rotated refresh token is treated as theft and kills the session."""
    first = (await client.post("/api/v1/auth/refresh",
                               json={"refresh_token": alice["refresh_token"]})).json()
    assert (await client.get("/api/v1/users/me",
                             headers=auth(first["access_token"]))).status_code == 200

    replay = await client.post("/api/v1/auth/refresh",
                               json={"refresh_token": alice["refresh_token"]})
    assert replay.status_code == 401
    # After reuse detection neither the stolen nor the rotated token is usable.
    assert (await client.get("/api/v1/users/me",
                             headers=auth(first["access_token"]))).status_code == 401


# --------------------------------------------------------------------------- #
# Sessions / devices
# --------------------------------------------------------------------------- #
async def test_sessions_can_be_listed_and_revoked(client, alice, settings):
    second = await client.post("/api/v1/auth/login", json={
        "username": alice["username"], "password": alice["password"],
        "device_name": "Tablet", "platform": "android",
    })
    assert second.status_code == 200
    second_pair = second.json()

    sessions = (await client.get("/api/v1/auth/sessions",
                                 headers=auth(second_pair["access_token"]))).json()
    assert len(sessions) >= 2
    current = [s for s in sessions if s["current"]]
    assert len(current) == 1
    assert {"id", "device_name", "platform", "created_at"} <= set(current[0])

    victim = next(s for s in sessions if not s["current"])
    revoked = await client.delete(f"/api/v1/auth/sessions/{victim['id']}",
                                  headers=auth(second_pair["access_token"]))
    assert revoked.status_code == 204

    assert (await client.get("/api/v1/users/me",
                             headers=auth(alice["access_token"]))).status_code == 401
    assert (await client.get("/api/v1/users/me",
                             headers=auth(second_pair["access_token"]))).status_code == 200


async def test_password_login_still_works_after_otp_account(client, alice):
    """The adapter registers a random password; it must remain usable for recovery."""
    login = await client.post("/api/v1/auth/login", json={
        "username": alice["username"], "password": alice["password"], "device_name": "Pixel",
    })
    assert login.status_code == 200
    assert login.json()["access_token"]


async def test_unknown_device_session_returns_404(client, alice):
    response = await client.delete(f"/api/v1/auth/sessions/{uuid.uuid4()}",
                                   headers=auth(alice["access_token"]))
    assert response.status_code == 404


async def test_logout_revokes_only_the_current_session(client, alice):
    other = (await client.post("/api/v1/auth/login", json={
        "username": alice["username"], "password": alice["password"], "device_name": "Laptop",
    })).json()
    assert (await client.post("/api/v1/auth/logout",
                              headers=auth(alice["access_token"]))).status_code == 204
    assert (await client.get("/api/v1/users/me",
                             headers=auth(alice["access_token"]))).status_code == 401
    assert (await client.get("/api/v1/users/me",
                             headers=auth(other["access_token"]))).status_code == 200


async def test_malformed_requests_are_validation_errors(client):
    assert (await client.post("/api/v1/auth/otp/verify", json={"username": "a"})).status_code == 422
    assert (await client.post("/api/v1/auth/otp/request", json={})).status_code == 422
    assert (await client.post("/api/v1/auth/register",
                              json={"username": "x", "password": "short"})).status_code == 422


async def test_registration_rejects_duplicate_phone_identity(client):
    user = await register_user(client, "phase2_dup")
    duplicate = await client.post("/api/v1/auth/register", json={
        "username": user["username"], "password": user["password"], "display_name": "Again",
    })
    assert duplicate.status_code in (409, 422)


# --------------------------------------------------------------------------- #
# Proof of phone ownership before an account exists
# --------------------------------------------------------------------------- #
async def test_registration_without_verification_is_refused(client):
    response = await client.post("/api/v1/auth/register", json={
        "username": "ph49170000001", "password": "Password123", "display_name": "No Proof",
    })
    assert response.status_code == 403
    assert "not verified" in response.json()["detail"].lower()

    # The account really does not exist afterwards.
    assert (await client.post("/api/v1/auth/otp/request",
                              json={"username": "ph49170000001"})).status_code == 404


async def test_registration_token_is_single_use(client):
    username = "ph49170000002"
    challenge = (await client.post("/api/v1/auth/otp/request",
                                   json={"username": username, "purpose": "register"})).json()
    token = challenge["registration_token"]
    assert token

    first = await client.post("/api/v1/auth/register", json={
        "username": username, "password": "Password123", "display_name": "Owner",
        "registration_token": token,
    })
    assert first.status_code == 201, first.text

    replay = await client.post("/api/v1/auth/register", json={
        "username": username, "password": "Password123", "display_name": "Impostor",
        "registration_token": token,
    })
    assert replay.status_code in (409, 403)


async def test_registration_token_is_bound_to_the_phone(client):
    """A token obtained for one number cannot create an account for another."""
    owner_token = (await client.post("/api/v1/auth/otp/request",
                                     json={"username": "ph49170000003",
                                           "purpose": "register"})).json()["registration_token"]
    stolen = await client.post("/api/v1/auth/register", json={
        "username": "ph49170000004", "password": "Password123", "display_name": "Thief",
        "registration_token": owner_token,
    })
    assert stolen.status_code == 403


async def test_registration_requires_a_delivered_code(client, monkeypatch):
    """If the gateway fails, no grant is handed out and sign-up stays impossible."""
    from app.routers import auth as auth_router
    from app.notifications import DeliveryError

    class BrokenSender:
        name = "twilio"

        async def send(self, destination, text):
            raise DeliveryError("gateway down")

    monkeypatch.setattr(auth_router, "build_sms_sender", lambda settings: BrokenSender())
    failed = await client.post("/api/v1/auth/otp/request",
                               json={"username": "ph49170000005", "purpose": "register"})
    assert failed.status_code == 502
    monkeypatch.undo()

    blocked = await client.post("/api/v1/auth/register", json={
        "username": "ph49170000005", "password": "Password123", "display_name": "Nope",
    })
    assert blocked.status_code == 403


async def test_registration_token_expires(client, app):
    username = "ph49170000006"
    token = (await client.post("/api/v1/auth/otp/request",
                               json={"username": username, "purpose": "register"})).json()[
        "registration_token"]
    from app.models import RegistrationGrant
    from sqlalchemy import select

    factory = app.state.runtime.session_factory
    async with factory() as db:
        grant = (await db.execute(
            select(RegistrationGrant).where(RegistrationGrant.token == token)
        )).scalar_one()
        grant.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await db.commit()

    expired = await client.post("/api/v1/auth/register", json={
        "username": username, "password": "Password123", "display_name": "Late",
        "registration_token": token,
    })
    assert expired.status_code == 403
    assert "expired" in expired.json()["detail"].lower()


async def test_registration_spends_the_code(client):
    """The code used for sign-up cannot then be used to open a session."""
    username = "ph49170000007"
    challenge = (await client.post("/api/v1/auth/otp/request",
                                   json={"username": username, "purpose": "register"})).json()
    registered = await client.post("/api/v1/auth/register", json={
        "username": username, "password": "Password123", "display_name": "Owner",
        "registration_token": challenge["registration_token"],
    })
    assert registered.status_code == 201
    reused = await client.post("/api/v1/auth/otp/verify",
                               json={"username": username, "code": challenge["debug_code"]})
    assert reused.status_code == 401
