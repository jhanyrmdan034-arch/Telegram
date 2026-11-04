"""Outbound one-time-code delivery.

The OTP flow is only real if the code actually leaves the server. This module
ships three concrete gateways behind one async interface:

* ``twilio``    — Twilio Programmable SMS REST API (international)
* ``kavenegar`` — Kavenegar REST API (Iranian numbers)
* ``webhook``   — any gateway that accepts ``{"to", "from", "text"}`` JSON
* ``log``       — development only: the code goes to the server log

Whatever the provider, the code is never returned to the caller unless
``OTP_DEBUG`` is explicitly enabled; in production that flag is rejected at
startup. A failed delivery surfaces as a 502 on the API so the client can say
"code was not sent" instead of waiting forever.
"""
from __future__ import annotations

import base64
import logging
from typing import TYPE_CHECKING, Protocol

import httpx

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .config import Settings

log = logging.getLogger("messenger.notifications")

TWILIO_BASE_URL = "https://api.twilio.com"
KAVENEGAR_BASE_URL = "https://api.kavenegar.com"
DEFAULT_TIMEOUT = 10.0


class SmsSender(Protocol):
    name: str

    async def send(self, destination: str, text: str) -> bool: ...


class DeliveryError(RuntimeError):
    """Raised when the gateway refused or could not accept the message."""


class LogSmsSender:
    """Development sender: writes the message to the application log."""

    name = "log"

    async def send(self, destination: str, text: str) -> bool:
        log.info("SMS to %s: %s", destination, text)
        return True


class TwilioSmsSender:
    name = "twilio"

    def __init__(self, account_sid: str, auth_token: str, from_number: str,
                 base_url: str = TWILIO_BASE_URL, timeout: float = DEFAULT_TIMEOUT) -> None:
        if not account_sid or not auth_token or not from_number:
            raise ValueError(
                "SMS_PROVIDER=twilio requires TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN "
                "and TWILIO_FROM_NUMBER"
            )
        self.account_sid = account_sid
        self.auth_token = auth_token
        self.from_number = from_number
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    async def send(self, destination: str, text: str) -> bool:
        url = f"{self.base_url}/2010-04-01/Accounts/{self.account_sid}/Messages.json"
        credentials = base64.b64encode(
            f"{self.account_sid}:{self.auth_token}".encode()).decode()
        payload = {"To": destination, "From": self.from_number, "Body": text}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                url, data=payload, headers={"Authorization": f"Basic {credentials}"}
            )
        if response.status_code >= 300:
            raise DeliveryError(
                f"Twilio rejected the message ({response.status_code}): {response.text[:200]}"
            )
        return True


class KavenegarSmsSender:
    name = "kavenegar"

    def __init__(self, api_key: str, sender: str, base_url: str = KAVENEGAR_BASE_URL,
                 timeout: float = DEFAULT_TIMEOUT) -> None:
        if not api_key:
            raise ValueError("SMS_PROVIDER=kavenegar requires KAVENEGAR_API_KEY")
        self.api_key = api_key
        self.sender = sender
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    async def send(self, destination: str, text: str) -> bool:
        url = f"{self.base_url}/v1/{self.api_key}/sms/send.json"
        params = {"receptor": destination, "message": text}
        if self.sender:
            params["sender"] = self.sender
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(url, params=params)
        if response.status_code >= 300:
            raise DeliveryError(
                f"Kavenegar rejected the message ({response.status_code}): {response.text[:200]}"
            )
        try:
            body = response.json()
        except ValueError:
            return True
        status = (body.get("return") or {}).get("status")
        if status not in (None, 200):
            raise DeliveryError(f"Kavenegar error {status}: {body.get('return')}")
        return True


class WebhookSmsSender:
    """Generic gateway: POST {"to", "from", "text"} with a bearer token."""

    name = "webhook"

    def __init__(self, url: str, token: str | None = None, from_number: str | None = None,
                 timeout: float = DEFAULT_TIMEOUT) -> None:
        if not url:
            raise ValueError("SMS_PROVIDER=webhook requires SMS_WEBHOOK_URL")
        self.url = url
        self.token = token
        self.from_number = from_number
        self.timeout = timeout

    async def send(self, destination: str, text: str) -> bool:
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        payload = {"to": destination, "from": self.from_number, "text": text}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(self.url, json=payload, headers=headers)
        if response.status_code >= 300:
            raise DeliveryError(
                f"SMS webhook rejected the message ({response.status_code}): "
                f"{response.text[:200]}"
            )
        return True


def build_sms_sender(settings: "Settings") -> SmsSender:
    """Selects the gateway from configuration; fails loudly when misconfigured."""
    provider = (settings.sms_provider or "log").strip().lower()
    if provider in {"log", ""}:
        return LogSmsSender()
    if provider == "twilio":
        return TwilioSmsSender(
            settings.twilio_account_sid or "",
            settings.twilio_auth_token or "",
            settings.twilio_from_number or "",
        )
    if provider == "kavenegar":
        return KavenegarSmsSender(
            settings.kavenegar_api_key or "",
            settings.kavenegar_sender or "",
        )
    if provider == "webhook":
        return WebhookSmsSender(
            settings.sms_webhook_url or "",
            settings.sms_webhook_token,
            settings.sms_webhook_from,
        )
    raise ValueError(f"Unsupported SMS_PROVIDER: {settings.sms_provider!r}")
