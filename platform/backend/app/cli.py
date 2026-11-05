"""Operational CLI: seeding, superuser bootstrap, health checks.

Usage:
    python -m app.cli seed
    python -m app.cli create-superuser --username root --password 'strong-password'
    python -m app.cli check
"""
from __future__ import annotations

import argparse
import asyncio
import sys

from sqlalchemy import select, text

from .bootstrap import seed
from .config import get_settings
from .db import create_engine, dispose_engine, get_session_factory
from .models import Role, User
from .security import hash_password


async def _seed() -> None:
    settings = get_settings()
    engine = create_engine(settings)
    factory = get_session_factory(settings)
    async with factory() as db:
        await seed(db)
    await engine.dispose()


async def _create_superuser(username: str, password: str, display_name: str) -> None:
    settings = get_settings()
    engine = create_engine(settings)
    from sqlalchemy.ext.asyncio import async_sessionmaker

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        await seed(db)
        role = (await db.execute(select(Role).where(Role.name == "super_admin"))).scalar_one()
        existing = (await db.execute(select(User).where(User.username == username))).scalar_one_or_none()
        if existing is not None:
            existing.admin_role_id = role.id
            existing.password_hash = hash_password(password)
            action = "updated"
        else:
            db.add(User(username=username, display_name=display_name,
                        password_hash=hash_password(password), admin_role_id=role.id))
            action = "created"
        await db.commit()
        print(f"superuser {username!r} {action}")
    await engine.dispose()


async def _seed_demo() -> None:
    settings = get_settings()
    if not settings.dev_login_enabled:
        print("DEV_LOGIN_ENABLED is false: demo accounts are not created")
        return
    engine = create_engine(settings)
    factory = get_session_factory(settings)
    from .devlogin import ensure_demo_accounts

    async with factory() as db:
        accounts = await ensure_demo_accounts(db)
        for account in accounts:
            print(f"demo account {account.username} ({account.id})")
    await engine.dispose()


async def _check() -> None:
    settings = get_settings()
    engine = create_engine(settings)
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
    print("database: ok")
    await engine.dispose()


async def _files_gc(older_than_hours: int) -> None:
    """Deletes stored objects that no message points at any more."""
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import select

    from .models import File, Message
    from .storage import LocalStorage

    settings = get_settings()
    engine = create_engine(settings)
    factory = get_session_factory(settings)
    storage = LocalStorage(settings.storage_dir)
    cutoff = datetime.now(UTC) - timedelta(hours=older_than_hours)
    removed = 0
    async with factory() as db:
        used = set((await db.execute(
            select(Message.file_id).where(Message.file_id.is_not(None))
        )).scalars().all())
        for record in (await db.execute(select(File))).scalars().all():
            if record.id in used or record.is_public:
                continue
            if record.created_at and record.created_at > cutoff:
                continue
            await storage.delete(record.storage_key)
            await db.delete(record)
            removed += 1
        await db.commit()
    await engine.dispose()
    print(f"files-gc: removed {removed} orphaned file(s)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.cli")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("seed", help="create system roles and default settings")

    su = sub.add_parser("create-superuser", help="create or promote a super admin")
    su.add_argument("--username", required=True)
    su.add_argument("--password", required=True)
    su.add_argument("--display-name", default="Super Admin")

    sub.add_parser("seed-demo", help="create the development test accounts (needs DEV_LOGIN_ENABLED)")
    sub.add_parser("check", help="verify database connectivity")
    gc = sub.add_parser("files-gc", help="delete stored files no message references any more")
    gc.add_argument("--older-than-hours", type=int, default=24)

    args = parser.parse_args(argv)
    if args.command == "seed":
        asyncio.run(_seed())
    elif args.command == "create-superuser":
        asyncio.run(_create_superuser(args.username, args.password, args.display_name))
    elif args.command == "seed-demo":
        asyncio.run(_seed_demo())
    elif args.command == "check":
        asyncio.run(_check())
    elif args.command == "files-gc":
        asyncio.run(_files_gc(args.older_than_hours))
    else:  # pragma: no cover
        parser.error("unknown command")
    return 0


if __name__ == "__main__":
    sys.exit(main())
