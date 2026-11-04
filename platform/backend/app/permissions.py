"""Role-Based Access Control: permission catalogue and role definitions."""
from __future__ import annotations

from enum import StrEnum


class Permission(StrEnum):
    USERS_READ = "users.read"
    USERS_WRITE = "users.write"
    USERS_BLOCK = "users.block"
    CHATS_READ = "chats.read"
    CHATS_WRITE = "chats.write"
    MESSAGES_READ = "messages.read"
    MESSAGES_DELETE = "messages.delete"
    REPORTS_READ = "reports.read"
    REPORTS_WRITE = "reports.write"
    TICKETS_READ = "tickets.read"
    TICKETS_WRITE = "tickets.write"
    ROLES_WRITE = "roles.write"
    SETTINGS_WRITE = "settings.write"
    AUDIT_READ = "audit.read"
    MONITORING_READ = "monitoring.read"
    STATS_READ = "stats.read"
    EXPORT_DATA = "export.data"


ALL_PERMISSIONS: tuple[str, ...] = tuple(p.value for p in Permission)

SYSTEM_ROLES: dict[str, dict] = {
    "super_admin": {
        "description": "Unrestricted platform access.",
        "permissions": list(ALL_PERMISSIONS),
    },
    "admin": {
        "description": "Full operational access except role editing.",
        "permissions": [
            p for p in ALL_PERMISSIONS if p != Permission.ROLES_WRITE.value
        ],
    },
    "moderator": {
        "description": "Content and account moderation.",
        "permissions": [
            Permission.USERS_READ.value,
            Permission.USERS_WRITE.value,
            Permission.USERS_BLOCK.value,
            Permission.CHATS_READ.value,
            Permission.MESSAGES_READ.value,
            Permission.MESSAGES_DELETE.value,
            Permission.REPORTS_READ.value,
            Permission.REPORTS_WRITE.value,
            Permission.STATS_READ.value,
        ],
    },
    "support": {
        "description": "User-facing support tickets.",
        "permissions": [
            Permission.USERS_READ.value,
            Permission.TICKETS_READ.value,
            Permission.TICKETS_WRITE.value,
            Permission.REPORTS_READ.value,
            Permission.STATS_READ.value,
        ],
    },
    "analyst": {
        "description": "Read-only analytics and exports.",
        "permissions": [
            Permission.USERS_READ.value,
            Permission.CHATS_READ.value,
            Permission.CHATS_WRITE.value,
            Permission.STATS_READ.value,
            Permission.MONITORING_READ.value,
            Permission.EXPORT_DATA.value,
            Permission.AUDIT_READ.value,
        ],
    },
    "technical_operator": {
        "description": "Infrastructure, monitoring and maintenance.",
        "permissions": [
            Permission.MONITORING_READ.value,
            Permission.SETTINGS_WRITE.value,
            Permission.STATS_READ.value,
            Permission.AUDIT_READ.value,
        ],
    },
}
