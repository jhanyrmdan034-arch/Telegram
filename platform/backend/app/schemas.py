"""Pydantic request/response schemas (also drive the generated OpenAPI document)."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

USERNAME_PATTERN = r"^[a-zA-Z][a-zA-Z0-9_]{3,31}$"


class APIModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --------------------------------------------------------------------------- #
# Auth
# --------------------------------------------------------------------------- #
class RegisterRequest(BaseModel):
    username: str = Field(pattern=USERNAME_PATTERN, examples=["alice_92"])
    password: str = Field(min_length=8, max_length=128)
    display_name: str = Field(min_length=1, max_length=128)
    device_name: str = Field(default="Unknown device", max_length=128)
    platform: Literal["android", "ios", "web", "desktop"] = "android"
    # Issued by POST /auth/otp/request (purpose "register") and required by default:
    # it proves a code was delivered to the phone before the account existed.
    registration_token: str | None = Field(default=None, max_length=64)

    @field_validator("username")
    @classmethod
    def _lower(cls, v: str) -> str:
        return v.strip()


class LoginRequest(BaseModel):
    username: str
    password: str
    device_name: str = Field(default="Unknown device", max_length=128)
    platform: Literal["android", "ios", "web", "desktop"] = "android"


class RefreshRequest(BaseModel):
    refresh_token: str


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class OtpRequest(BaseModel):
    username: str = Field(min_length=4, max_length=64)
    purpose: Literal["login", "register"] = "login"


class OtpChallenge(BaseModel):
    delivery: Literal["sms", "log"]
    expires_in: int
    debug_code: str | None = None
    # Only present when purpose == "register" and the account does not exist yet.
    registration_token: str | None = None


class DevLoginRequest(BaseModel):
    """Development-only sign-in for a seeded test account."""

    account: str = Field(min_length=3, max_length=64)
    device_name: str = Field(default="Development device", max_length=128)
    platform: Literal["android", "ios", "web", "desktop"] = "android"


class OtpVerifyRequest(BaseModel):
    username: str = Field(min_length=4, max_length=64)
    code: str = Field(min_length=4, max_length=8)
    device_name: str = Field(default="Unknown device", max_length=128)
    platform: Literal["android", "ios", "web", "desktop"] = "android"


class SessionOut(APIModel):
    id: uuid.UUID
    device_name: str | None = None
    platform: str | None = None
    ip: str | None = None
    created_at: datetime
    last_used_at: datetime | None = None
    current: bool = False


# --------------------------------------------------------------------------- #
# Users
# --------------------------------------------------------------------------- #
class UserPublic(APIModel):
    id: uuid.UUID
    username: str
    display_name: str
    bio: str | None = None
    avatar_file_id: uuid.UUID | None = None
    last_seen_at: datetime | None = None


class UserPrivate(UserPublic):
    settings: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class UserUpdate(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=128)
    bio: str | None = Field(default=None, max_length=512)
    avatar_file_id: uuid.UUID | None = None
    settings: dict[str, Any] | None = None


# --------------------------------------------------------------------------- #
# Contacts
# --------------------------------------------------------------------------- #
class ContactCreate(BaseModel):
    contact_id: uuid.UUID
    first_name: str | None = Field(default=None, max_length=64)
    last_name: str | None = Field(default=None, max_length=64)


class ContactOut(APIModel):
    contact_id: uuid.UUID
    first_name: str | None
    last_name: str | None
    username: str
    display_name: str
    created_at: datetime


# --------------------------------------------------------------------------- #
# Chats
# --------------------------------------------------------------------------- #
class ChatCreate(BaseModel):
    type: Literal["private", "group", "channel", "saved"]
    title: str | None = Field(default=None, max_length=255)
    about: str | None = Field(default=None, max_length=512)
    member_ids: list[uuid.UUID] = Field(default_factory=list, max_length=200)
    username: str | None = Field(default=None, max_length=64)
    is_public: bool = False


class ChatUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=255)
    about: str | None = Field(default=None, max_length=512)
    username: str | None = Field(default=None, max_length=64)
    is_public: bool | None = None
    avatar_file_id: uuid.UUID | None = None


class ChatOut(APIModel):
    id: uuid.UUID
    type: str
    title: str | None
    username: str | None
    about: str | None
    avatar_file_id: uuid.UUID | None
    owner_id: uuid.UUID | None
    is_public: bool
    member_count: int
    last_seq: int
    created_at: datetime
    my_role: str | None = None
    last_read_seq: int = 0
    # Fields the client's dialog list needs to render a row without extra calls.
    unread_count: int = 0
    pinned: bool = False
    last_message: dict[str, Any] | None = None
    peer: dict[str, Any] | None = None


class ChatMemberOut(BaseModel):
    user_id: uuid.UUID
    username: str
    display_name: str
    role: str
    joined_at: datetime


class ForwardRequest(BaseModel):
    chat_ids: list[uuid.UUID] = Field(min_length=1, max_length=20)


class ChatPin(BaseModel):
    pinned: bool = True


class FolderCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    position: int = Field(default=0, ge=0, le=1000)


class FolderUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    position: int | None = Field(default=None, ge=0, le=1000)


class FolderOut(APIModel):
    id: uuid.UUID
    name: str
    position: int
    created_at: datetime
    chat_ids: list[uuid.UUID] = Field(default_factory=list)


class FolderChatsUpdate(BaseModel):
    chat_ids: list[uuid.UUID] = Field(max_length=500)


class PublicConfig(BaseModel):
    """Served to unauthenticated clients so the UI can bootstrap before login."""

    platform: str = "messenger"
    registration_enabled: bool
    default_region: str = "global"
    max_file_size_bytes: int
    max_message_length: int
    max_group_members: int
    calls_enabled: bool
    stickers_enabled: bool
    maintenance: bool
    android_min_version: str
    android_latest_version: str
    otp_required: bool


class MemberAdd(BaseModel):
    user_ids: list[uuid.UUID] = Field(min_length=1, max_length=200)
    role: Literal["admin", "member"] = "member"


class MemberUpdate(BaseModel):
    role: Literal["admin", "member"]


# --------------------------------------------------------------------------- #
# Messages
# --------------------------------------------------------------------------- #
class MessageCreate(BaseModel):
    type: Literal["text", "photo", "video", "document", "voice", "sticker", "location", "poll"] = "text"
    text: str | None = Field(default=None, max_length=8192)
    entities: list[dict[str, Any]] | None = None
    reply_to_id: uuid.UUID | None = None
    file_id: uuid.UUID | None = None
    meta: dict[str, Any] = Field(default_factory=dict)
    client_message_id: str | None = Field(default=None, max_length=64)


class MessageUpdate(BaseModel):
    text: str = Field(min_length=1, max_length=8192)


class MessageOut(APIModel):
    id: uuid.UUID
    chat_id: uuid.UUID
    sender_id: uuid.UUID | None
    seq: int
    type: str
    text: str | None
    entities: list[dict[str, Any]] | None = None
    reply_to_id: uuid.UUID | None
    file_id: uuid.UUID | None
    meta: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    edited_at: datetime | None = None


class MessagePage(BaseModel):
    items: list[MessageOut]
    next_before_seq: int | None = None


# --------------------------------------------------------------------------- #
# Files
# --------------------------------------------------------------------------- #
class FileOut(APIModel):
    id: uuid.UUID
    name: str
    mime_type: str
    size: int
    kind: str
    sha256: str
    width: int | None = None
    height: int | None = None
    duration: int | None = None
    download_url: str | None = None


# --------------------------------------------------------------------------- #
# Calls
# --------------------------------------------------------------------------- #
class CallCreate(BaseModel):
    kind: Literal["audio", "video"] = "audio"
    participant_ids: list[uuid.UUID] = Field(default_factory=list)


class CallOut(APIModel):
    id: uuid.UUID
    chat_id: uuid.UUID
    initiator_id: uuid.UUID
    kind: str
    status: str
    participant_ids: list[uuid.UUID]
    created_at: datetime
    started_at: datetime | None = None
    ended_at: datetime | None = None


class CallUpdate(BaseModel):
    status: Literal["active", "ended", "declined", "missed", "failed"]
    end_reason: str | None = Field(default=None, max_length=32)


# --------------------------------------------------------------------------- #
# Support / moderation
# --------------------------------------------------------------------------- #
class ReportCreate(BaseModel):
    target_type: Literal["user", "chat", "message"]
    target_id: uuid.UUID
    reason: str = Field(min_length=2, max_length=64)
    description: str | None = Field(default=None, max_length=2000)


class ReportOut(APIModel):
    id: uuid.UUID
    reporter_id: uuid.UUID | None
    target_type: str
    target_id: uuid.UUID
    reason: str
    description: str | None
    status: str
    resolution: str | None
    created_at: datetime


class ReportResolve(BaseModel):
    status: Literal["reviewing", "resolved", "rejected"]
    resolution: str | None = Field(default=None, max_length=2000)


class TicketCreate(BaseModel):
    subject: str = Field(min_length=3, max_length=255)
    category: str = Field(default="general", max_length=32)
    priority: Literal["low", "normal", "high", "urgent"] = "normal"
    body: str = Field(min_length=1, max_length=8000)


class TicketMessageCreate(BaseModel):
    body: str = Field(min_length=1, max_length=8000)


class TicketMessageOut(APIModel):
    id: uuid.UUID
    author_id: uuid.UUID | None
    is_staff: bool
    body: str
    created_at: datetime


class TicketOut(APIModel):
    id: uuid.UUID
    user_id: uuid.UUID
    subject: str
    category: str
    priority: str
    status: str
    assigned_to: uuid.UUID | None
    created_at: datetime
    updated_at: datetime


class TicketAssign(BaseModel):
    assigned_to: uuid.UUID | None
    priority: Literal["low", "normal", "high", "urgent"] | None = None
    status: Literal["open", "pending", "resolved", "closed"] | None = None


# --------------------------------------------------------------------------- #
# Admin
# --------------------------------------------------------------------------- #
class AdminUserOut(APIModel):
    id: uuid.UUID
    username: str
    display_name: str
    is_active: bool
    is_blocked: bool
    block_reason: str | None
    suspended_until: datetime | None
    restriction_reason: str | None
    last_seen_at: datetime | None
    created_at: datetime
    role: str | None = None


class UserBlock(BaseModel):
    blocked: bool
    reason: str | None = Field(default=None, max_length=512)


class UserSuspend(BaseModel):
    until: datetime | None
    reason: str | None = Field(default=None, max_length=512)


class RoleOut(APIModel):
    id: uuid.UUID
    name: str
    description: str | None
    permissions: list[str]
    is_system: bool


class RoleCreate(BaseModel):
    name: str = Field(min_length=2, max_length=64, pattern=r"^[a-z_]+$")
    description: str | None = Field(default=None, max_length=255)
    permissions: list[str]


class RoleUpdate(BaseModel):
    description: str | None = Field(default=None, max_length=255)
    permissions: list[str] | None = None


class AdminAssignRole(BaseModel):
    role: str  # role name, or "none" to remove administrative access


class AuditOut(APIModel):
    id: uuid.UUID
    actor_id: uuid.UUID | None
    action: str
    target_type: str | None
    target_id: str | None
    ip: str | None
    meta: dict[str, Any]
    created_at: datetime


class SettingOut(APIModel):
    key: str
    value: dict[str, Any]
    updated_at: datetime


class SettingsUpdate(BaseModel):
    values: dict[str, dict[str, Any]]


class DashboardStats(BaseModel):
    total_users: int
    online_users: int
    active_today: int
    active_month: int
    new_users_today: int
    total_messages: int
    messages_today: int
    total_groups: int
    total_channels: int
    total_files: int
    storage_bytes: int
    open_reports: int
    open_tickets: int


class TimeseriesPoint(BaseModel):
    date: str
    users: int
    messages: int


class SystemHealth(BaseModel):
    database: str
    redis: str
    api: str
    websocket_connections: int
    cpu_percent: float
    memory_percent: float
    memory_used_bytes: int
    disk_percent: float
    uptime_seconds: int
    version: str
