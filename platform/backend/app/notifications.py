"""Outbound notification channels.

The OTP flow needs a code delivery mechanism. A pluggable interface keeps the API
honest: `LogSmsSender` is what runs today (codes go to the server log and, when
`OTP_DEBUG` is enabled, back to the caller for local testing). A production SMS
gateway is a drop-in implementation of `SmsSender` — no API changes required.
"""
from __future__ import annotations

import logging
from typing import Protocol

log = logging.getLogger("messenger.notifications")


class SmsSender(Protocol):
    name: str

    async def send(self, destination: str, text: str) -> bool: ...


class LogSmsSender:
    """Development sender: writes the message to the application log."""

    name = "log"

    async def send(self, destination: str, text: str) -> bool:
        log.info("SMS to %s: %s", destination, text)
        return True


def build_sms_sender(provider: str) -> SmsSender:
    # Additional providers implement the same protocol and are selected here.
    if provider in {"log", "", None}:  # type: ignore[comparison-overlap]
        return LogSmsSender()
    raise ValueError(f"Unsupported SMS_PROVIDER: {provider!r}")
