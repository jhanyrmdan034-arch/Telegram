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


async def _check() -> None:
    settings = get_settings()
    engine = create_engine(settings)
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
    print("database: ok")
    await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.cli")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("seed", help="create system roles and default settings")

    su = sub.add_parser("create-superuser", help="create or promote a super admin")
    su.add_argument("--username", required=True)
    su.add_argument("--password", required=True)
    su.add_argument("--display-name", default="Super Admin")

    sub.add_parser("check", help="verify database connectivity")

    args = parser.parse_args(argv)
    if args.command == "seed":
        asyncio.run(_seed())
    elif args.command == "create-superuser":
        asyncio.run(_create_superuser(args.username, args.password, args.display_name))
    elif args.command == "check":
        asyncio.run(_check())
    else:  # pragma: no cover
        parser.error("unknown command")
    return 0


if __name__ == "__main__":
    sys.exit(main())
