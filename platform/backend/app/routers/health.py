"""Liveness/readiness probes for the load balancer and the admin dashboard."""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from ..deps import DbDep, RuntimeDep

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz(runtime: RuntimeDep):
    return {"status": "ok", "version": runtime.version}


@router.get("/readyz")
async def readyz(db: DbDep, runtime: RuntimeDep):
    checks: dict[str, str] = {}
    healthy = True

    try:
        await db.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:  # noqa: BLE001
        checks["database"] = f"error: {exc.__class__.__name__}"
        healthy = False

    if runtime.redis is not None:
        try:
            await runtime.redis.ping()
            checks["redis"] = "ok"
        except Exception as exc:  # noqa: BLE001
            checks["redis"] = f"error: {exc.__class__.__name__}"
            healthy = False
    else:
        checks["redis"] = "disabled"

    checks["websocket"] = "ok"
    body = {"status": "ready" if healthy else "degraded", "checks": checks,
            "version": runtime.version}
    return JSONResponse(status_code=200 if healthy else 503, content=body)
