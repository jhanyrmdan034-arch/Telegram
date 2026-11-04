"""FastAPI application factory."""
from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from .bootstrap import seed
from .config import Settings, get_settings
from .db import create_engine
from .models import AppSetting, ErrorLog
from .ratelimit import client_ip
from .routers import (
    admin, auth, calls, chats, contacts, files, folders, health, messages, meta, support,
    users, ws,
)
from .runtime import build_runtime

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("messenger")

# During maintenance the client must still be able to authenticate and read its
# bootstrap configuration, otherwise it can only show a blank screen.
MAINTENANCE_EXEMPT_PREFIXES = (
    "/docs", "/redoc", "/openapi.json", "/api/v1/health", "/api/v1/admin", "/api/v1/auth",
    "/api/v1/config",
)


def create_app(settings: Settings | None = None, engine=None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings.validate_for_production()
        # The app owns its engine so that a process (or a test) never reuses
        # connections bound to a different event loop.
        app_engine = engine or create_engine(settings)
        from sqlalchemy.ext.asyncio import async_sessionmaker

        session_factory = async_sessionmaker(app_engine, expire_on_commit=False)

        runtime = await build_runtime(settings, app_engine, session_factory)
        app.state.runtime = runtime
        await runtime.start()
        async with session_factory() as db:
            await seed(db)
        log.info("messaging platform API ready (env=%s)", settings.environment)
        try:
            yield
        finally:
            await runtime.stop()

    app = FastAPI(
        title="Messaging Platform API",
        version="1.0.0",
        description=(
            "Proprietary backend for the independent messaging platform. "
            "Replaces every Telegram backend dependency with owned infrastructure: "
            "authentication, chats, groups, channels, media, calls, moderation and support."
        ),
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"] if not settings.is_production else [],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Shared with the admin router so a toggle takes effect on the next request.
    maintenance_cache: dict[str, Any] = {"checked_at": 0.0, "enabled": False, "message": ""}
    app.state.maintenance_cache = maintenance_cache

    @app.middleware("http")
    async def platform_guards(request: Request, call_next):
        runtime = getattr(request.app.state, "runtime", None)
        if runtime is None:
            return await call_next(request)

        path = request.url.path

        if settings.rate_limit_enabled and not path.startswith(("/docs", "/redoc", "/openapi.json")):
            try:
                await runtime.limiter.enforce(f"ip:{client_ip(request)}")
            except Exception as exc:  # HTTPException is raised by enforce
                if getattr(exc, "status_code", None) == status.HTTP_429_TOO_MANY_REQUESTS:
                    return JSONResponse({"detail": "Too many requests"}, status_code=429,
                                        headers=getattr(exc, "headers", None))
                raise

        # Maintenance mode is cached for a few seconds to keep it off the hot path.
        now = time.time()
        if now - maintenance_cache["checked_at"] > 5:
            try:
                async with runtime.session_factory() as db:
                    row = await db.get(AppSetting, "maintenance")
                    value = row.value if row else {}
                maintenance_cache.update(checked_at=now, enabled=bool(value.get("enabled")),
                                         message=value.get("message", "Scheduled maintenance"))
            except Exception:  # database might still be starting
                maintenance_cache["checked_at"] = now

        if maintenance_cache["enabled"] and not path.startswith(MAINTENANCE_EXEMPT_PREFIXES):
            return JSONResponse(
                {"detail": "Platform is under maintenance",
                 "message": maintenance_cache["message"]},
                status_code=503,
            )

        return await call_next(request)

    @app.exception_handler(Exception)
    async def unhandled_exception(request: Request, exc: Exception):  # pragma: no cover
        log.exception("unhandled error on %s %s", request.method, request.url.path)
        runtime = getattr(request.app.state, "runtime", None)
        if runtime is not None:
            try:
                import traceback

                async with runtime.session_factory() as db:
                    db.add(ErrorLog(
                        message=f"{exc.__class__.__name__}: {exc}"[:2000],
                        path=request.url.path[:255],
                        method=request.method,
                        traceback="".join(traceback.format_exception(exc))[:8000],
                    ))
                    await db.commit()
            except Exception:  # never mask the original failure
                log.exception("could not persist error log entry")
        return JSONResponse({"detail": "Internal server error"}, status_code=500)

    api_prefix = "/api/v1"
    for router in (auth.router, users.router, contacts.router, chats.router, messages.router,
                   files.router, calls.router, folders.router, support.router, meta.router,
                   admin.router):
        app.include_router(router, prefix=api_prefix)
    app.include_router(health.router, prefix=api_prefix)
    app.include_router(ws.router, prefix=api_prefix)

    if settings.admin_dir.is_dir():
        app.mount("/admin", StaticFiles(directory=str(settings.admin_dir), html=True), name="admin")

        @app.get("/admin/", include_in_schema=False)
        async def admin_index_redirect():  # pragma: no cover - static mount wins first
            return RedirectResponse("/admin/index.html")

    @app.get("/", include_in_schema=False)
    async def root():
        return {
            "name": "Messaging Platform API",
            "version": "1.0.0",
            "docs": "/docs",
            "openapi": "/openapi.json",
            "health": "/api/v1/healthz",
        }

    return app


app = create_app()
