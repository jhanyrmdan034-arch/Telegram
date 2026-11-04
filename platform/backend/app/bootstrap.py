"""Idempotent seeding of system roles and default platform settings."""
from __future__ import annotations

from sqlalchemy import select

from .models import AppSetting, Role
from .permissions import SYSTEM_ROLES

DEFAULT_SETTING_ROWS = {
    "registration": {"enabled": True, "require_approval": False},
    "security": {"max_sessions_per_user": 10, "password_min_length": 8},
    "limits": {"max_file_size_bytes": 209715200, "max_message_length": 8192,
               "max_group_members": 5000},
    "notifications": {"push_enabled": True},
    "maintenance": {"enabled": False, "message": "Scheduled maintenance"},
    "app_versions": {"android_min": "1.0.0", "android_latest": "1.0.0"},
    "retention": {"messages_days": 0, "audit_days": 365},
}


async def seed(db) -> None:
    for name, spec in SYSTEM_ROLES.items():
        role = (await db.execute(select(Role).where(Role.name == name))).scalar_one_or_none()
        if role is None:
            db.add(Role(name=name, description=spec["description"],
                        permissions=spec["permissions"], is_system=True))
        else:
            # Keep system role permissions in sync with the code while preserving custom roles.
            role.permissions = spec["permissions"]
            role.description = spec["description"]

    for key, value in DEFAULT_SETTING_ROWS.items():
        existing = await db.get(AppSetting, key)
        if existing is None:
            db.add(AppSetting(key=key, value=value))

    await db.commit()
