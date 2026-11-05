"""Development-only passwordless sign-in for two seeded test accounts.

Purpose: try the messaging features from the real Android app with two
independent users without going through SMS delivery.

Safety rules, all enforced here and in ``app/routers/auth.py``:
  * only when ``DEV_LOGIN_ENABLED=true`` **and** the environment is not production;
  * only accounts flagged ``is_test_account`` — never a real user's account;
  * the login still creates a normal, revocable session with normal token TTLs;
  * every use is written to the security event log.
"""
from __future__ import annotations

import logging
import secrets
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import Settings
from .models import User
from .security import hash_password

log = logging.getLogger("messenger.devlogin")

# (username, display_name, bio) — fixed ids are not needed, usernames are unique.
DEMO_ACCOUNTS: tuple[tuple[str, str, str], ...] = (
    ("demo_alice", "Alice Demo", "Test account A — used for local messaging tests"),
    ("demo_bob", "Bob Demo", "Test account B — used for local messaging tests"),
)

DEMO_USERNAMES = tuple(account[0] for account in DEMO_ACCOUNTS)


def dev_login_enabled(settings: Settings) -> bool:
    """The single source of truth for the feature flag."""
    return bool(settings.dev_login_enabled) and not settings.is_production


async def ensure_demo_accounts(db: AsyncSession) -> list[User]:
    """Creates (or repairs) the test accounts; idempotent, safe on every start."""
    created: list[User] = []
    for username, display_name, bio in DEMO_ACCOUNTS:
        user = (await db.execute(
            select(User).where(User.username == username)
        )).scalar_one_or_none()
        if user is None:
            # A random password that nobody knows: the account is reachable only
            # through the dev-login endpoint, and can be revoked like any other.
            user = User(
                username=username,
                display_name=display_name,
                bio=bio,
                password_hash=hash_password(secrets.token_urlsafe(32)),
                is_test_account=True,
                settings={"demo": True},
            )
            db.add(user)
            await db.flush()
            log.info("created demo account %s (%s)", username, user.id)
        else:
            # Keep the flag true even if the row predates it, so the separation
            # rule (only test accounts may use dev login) cannot drift.
            user.is_test_account = True
            if user.display_name:
                pass
            else:
                user.display_name = display_name
        created.append(user)
    await db.commit()
    return created


async def find_demo_user(db: AsyncSession, username: str) -> User | None:
    """Only flagged test accounts are eligible — a real account is never returned."""
    if username not in DEMO_USERNAMES:
        return None
    return (await db.execute(
        select(User).where(User.username == username, User.is_test_account.is_(True))
    )).scalar_one_or_none()


def demo_user_id(username: str) -> uuid.UUID | None:
    """Deterministic id helper for tests that need the account id before login."""
    if username not in DEMO_USERNAMES:
        return None
    return uuid.uuid5(uuid.NAMESPACE_URL, f"messenger:demo:{username}")
