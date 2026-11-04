"""Public bootstrap configuration for clients (the `help.getAppConfig` equivalent)."""
from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import select

from ..deps import DbDep, RuntimeDep
from ..models import AppSetting
from ..schemas import PublicConfig

router = APIRouter(tags=["config"])


@router.get("/config", response_model=PublicConfig)
async def public_config(db: DbDep, runtime: RuntimeDep):
    """Unauthenticated: the client reads this before showing the login screen."""
    settings = {row.key: row.value for row in (await db.execute(select(AppSetting))).scalars()}

    registration = settings.get("registration", {})
    limits = settings.get("limits", {})
    versions = settings.get("app_versions", {})
    maintenance = settings.get("maintenance", {})

    return PublicConfig(
        registration_enabled=bool(registration.get("enabled", True)),
        max_file_size_bytes=int(limits.get("max_file_size_bytes", runtime.settings.max_file_size_bytes)),
        max_message_length=int(limits.get("max_message_length", 8192)),
        max_group_members=int(limits.get("max_group_members", 5000)),
        calls_enabled=True,
        stickers_enabled=True,
        maintenance=bool(maintenance.get("enabled", False)),
        android_min_version=str(versions.get("android_min", "1.0.0")),
        android_latest_version=str(versions.get("android_latest", "1.0.0")),
        otp_required=False,
    )
