"""Test fixtures: a real PostgreSQL 16 instance provided by `pgserver`.

No mocks of the database layer are used — every test exercises the real schema,
real SQL and real transaction behaviour.
"""
from __future__ import annotations

import uuid
from pathlib import Path

import httpx
import pgserver
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.pool import NullPool
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import Settings
from app.main import create_app
from app.models import Base

TABLES = (
    "users, devices, sessions, contacts, blocked_users, chats, chat_members, messages, files,"
    " sticker_sets, stickers, calls, reports, tickets, ticket_messages, audit_log,"
    " security_events, error_log, app_settings, roles"
)


@pytest.fixture(scope="session")
def database_url(tmp_path_factory) -> str:
    data_dir = tmp_path_factory.mktemp("pgdata")
    server = pgserver.get_server(str(data_dir), cleanup_mode="stop")
    url = server.get_uri().replace("postgresql://", "postgresql+asyncpg://", 1)

    import asyncio

    async def _create_schema() -> None:
        engine = create_async_engine(url, poolclass=None)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await engine.dispose()

    asyncio.run(_create_schema())
    yield url
    server.cleanup()


@pytest.fixture(scope="session")
def settings(database_url: str, tmp_path_factory) -> Settings:
    storage = tmp_path_factory.mktemp("storage")
    return Settings(
        environment="test",
        debug=False,
        database_url=database_url,
        redis_url="memory://",  # in-process bus and counters
        jwt_secret="test-secret-value-that-is-long-enough-0123456789",
        access_token_ttl_seconds=900,
        refresh_token_ttl_seconds=86400,
        storage_dir=storage,
        max_file_size_bytes=1024 * 1024,
        rate_limit_enabled=False,
        public_base_url="http://test",
    )


@pytest_asyncio.fixture
async def app(settings: Settings):
    # A dedicated engine per test: connections must not cross event loops.
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE TABLE {TABLES} CASCADE"))

    application = create_app(settings=settings, engine=engine)
    async with application.router.lifespan_context(application):
        yield application
    await engine.dispose()


@pytest_asyncio.fixture
async def client(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http_client:
        yield http_client


@pytest_asyncio.fixture
async def db_session(app):
    async with app.state.runtime.session_factory() as session:
        yield session


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
async def register_user(client: httpx.AsyncClient, username: str | None = None,
                        password: str = "SuperSecret123") -> dict:
    username = username or f"user_{uuid.uuid4().hex[:10]}"
    response = await client.post(
        "/api/v1/auth/register",
        json={"username": username, "password": password, "display_name": username.title(),
              "device_name": "pytest", "platform": "android"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    body["username"] = username
    body["password"] = password
    return body


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def alice(client) -> dict:
    return await register_user(client, "alice_test")


@pytest_asyncio.fixture
async def bob(client) -> dict:
    return await register_user(client, "bob_test")


@pytest_asyncio.fixture
async def staff_user(client, db_session):
    """A super admin created the same way operations would bootstrap one."""
    from sqlalchemy import select

    from app.models import Role, User
    from app.security import hash_password

    role = (await db_session.execute(select(Role).where(Role.name == "super_admin"))).scalar_one()
    user = User(
        username="root_admin",
        display_name="Root Admin",
        password_hash=hash_password("RootPassword123"),
        admin_role_id=role.id,
    )
    db_session.add(user)
    await db_session.commit()

    response = await client.post(
        "/api/v1/auth/login",
        json={"username": "root_admin", "password": "RootPassword123", "device_name": "admin"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    body["username"] = "root_admin"
    body["id"] = str(user.id)
    return body


@pytest_asyncio.fixture
async def support_user(client, db_session):
    from sqlalchemy import select

    from app.models import Role, User
    from app.security import hash_password

    role = (await db_session.execute(select(Role).where(Role.name == "support"))).scalar_one()
    user = User(
        username="support_agent",
        display_name="Support Agent",
        password_hash=hash_password("SupportPassword123"),
        admin_role_id=role.id,
    )
    db_session.add(user)
    await db_session.commit()

    response = await client.post(
        "/api/v1/auth/login",
        json={"username": "support_agent", "password": "SupportPassword123", "device_name": "admin"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    body["username"] = "support_agent"
    body["id"] = str(user.id)
    return body


@pytest.fixture
def storage_dir(settings: Settings) -> Path:
    return Path(settings.storage_dir)
