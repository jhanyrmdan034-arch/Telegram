"""The Alembic migration must reproduce the models exactly, and be reversible."""
from __future__ import annotations

import asyncio
import os
import uuid
from pathlib import Path

import asyncpg
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url

from app.models import Base

ROOT = Path(__file__).resolve().parent.parent


def _admin_conn_kwargs(url) -> dict:
    kwargs = {"user": url.username or "postgres"}
    host = url.query.get("host")
    if host:
        kwargs["host"] = host
    return kwargs


def test_migration_matches_models_and_is_reversible(settings):
    url = make_url(settings.database_url)
    database = f"mig_{uuid.uuid4().hex[:8]}"

    async def create_database() -> None:
        conn = await asyncpg.connect(database="postgres", **_admin_conn_kwargs(url))
        await conn.execute(f'CREATE DATABASE "{database}"')
        await conn.close()

    asyncio.run(create_database())

    new_url = url.set(database=database)
    os.environ["DATABASE_URL"] = new_url.render_as_string(hide_password=False)

    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "alembic"))

    try:
        command.upgrade(config, "head")

        async def table_names() -> set[str]:
            conn = await asyncpg.connect(database=database, **_admin_conn_kwargs(url))
            rows = await conn.fetch(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
            )
            await conn.close()
            return {row["tablename"] for row in rows}

        migrated = asyncio.run(table_names())
        expected = set(Base.metadata.tables)
        assert expected.issubset(migrated), f"missing tables: {sorted(expected - migrated)}"
        assert "alembic_version" in migrated

        command.downgrade(config, "base")

        async def remaining_tables() -> set[str]:
            conn = await asyncpg.connect(database=database, **_admin_conn_kwargs(url))
            rows = await conn.fetch(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
            )
            await conn.close()
            return {row["tablename"] for row in rows}

        assert asyncio.run(remaining_tables()) <= {"alembic_version"}
    finally:
        os.environ.pop("DATABASE_URL", None)

        async def drop_database() -> None:
            conn = await asyncpg.connect(database="postgres", **_admin_conn_kwargs(url))
            await conn.execute(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)')
            await conn.close()

        asyncio.run(drop_database())
