"""Administration API: dashboard, moderation, roles, support, audit, exports."""
from __future__ import annotations

import csv
import io
import time
import uuid
from datetime import UTC, datetime, timedelta

import psutil
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from sqlalchemy import func, or_, select, text

from ..deps import CurrentUser, DbDep, RuntimeDep, permissions_of, require_permission
from ..models import (
    AppSetting,
    AuditLog,
    Call,
    Chat,
    ChatMember,
    ErrorLog,
    File,
    Message,
    Report,
    Role,
    SecurityEvent,
    Ticket,
    TicketMessage,
    User,
)
from ..permissions import ALL_PERMISSIONS, Permission, SYSTEM_ROLES
from ..schemas import (
    AdminUserOut,
    AuditOut,
    DashboardStats,
    ReportOut,
    ReportResolve,
    RoleCreate,
    RoleOut,
    RoleUpdate,
    SettingOut,
    SettingsUpdate,
    SystemHealth,
    TicketMessageCreate,
    TicketMessageOut,
    TicketOut,
    TimeseriesPoint,
    UserBlock,
    UserSuspend,
)
from ..services import audit as audit_log
from ..services import emit

router = APIRouter(prefix="/admin", tags=["admin"])


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
async def _scalar(db, sql: str, **params):
    result = await db.execute(text(sql), params)
    value = result.scalar()
    return value or 0


def _role_name(user: User) -> str | None:
    return user.admin_role.name if user.admin_role else None


# --------------------------------------------------------------------------- #
# Dashboard
# --------------------------------------------------------------------------- #
@router.get("/me")
async def admin_me(user: CurrentUser):
    role = user.admin_role
    return {
        "id": str(user.id),
        "username": user.username,
        "display_name": user.display_name,
        "role": role.name if role else None,
        "role_description": role.description if role else None,
        "permissions": sorted(permissions_of(user)),
    }


@router.get("/stats", response_model=DashboardStats)
async def dashboard_stats(
    db: DbDep,
    runtime: RuntimeDep,
    user: User = Depends(require_permission(Permission.STATS_READ)),
):
    total_users = await _scalar(db, "SELECT count(*) FROM users")
    active_today = await _scalar(
        db, "SELECT count(*) FROM users WHERE last_seen_at >= date_trunc('day', now())"
    )
    active_month = await _scalar(
        db, "SELECT count(*) FROM users WHERE last_seen_at >= now() - interval '30 days'"
    )
    new_users_today = await _scalar(
        db, "SELECT count(*) FROM users WHERE created_at >= date_trunc('day', now())"
    )
    total_messages = await _scalar(db, "SELECT count(*) FROM messages WHERE deleted_at IS NULL")
    messages_today = await _scalar(
        db, "SELECT count(*) FROM messages WHERE created_at >= date_trunc('day', now())"
    )
    total_groups = await _scalar(db, "SELECT count(*) FROM chats WHERE type = 'group'")
    total_channels = await _scalar(db, "SELECT count(*) FROM chats WHERE type = 'channel'")
    total_files = await _scalar(db, "SELECT count(*) FROM files")
    storage_bytes = await _scalar(db, "SELECT coalesce(sum(size), 0) FROM files")
    open_reports = await _scalar(db, "SELECT count(*) FROM reports WHERE status IN ('open','reviewing')")
    open_tickets = await _scalar(db, "SELECT count(*) FROM tickets WHERE status IN ('open','pending')")

    return DashboardStats(
        total_users=total_users,
        online_users=len(runtime.manager.online_user_ids()),
        active_today=active_today,
        active_month=active_month,
        new_users_today=new_users_today,
        total_messages=total_messages,
        messages_today=messages_today,
        total_groups=total_groups,
        total_channels=total_channels,
        total_files=total_files,
        storage_bytes=int(storage_bytes),
        open_reports=open_reports,
        open_tickets=open_tickets,
    )


@router.get("/stats/timeseries", response_model=list[TimeseriesPoint])
async def timeseries(
    db: DbDep,
    days: int = Query(default=14, ge=1, le=90),
    user: User = Depends(require_permission(Permission.STATS_READ)),
):
    rows = (await db.execute(
        text("""
            WITH calendar AS (
                SELECT generate_series(
                    date_trunc('day', now()) - (:days - 1) * interval '1 day',
                    date_trunc('day', now()),
                    interval '1 day'
                )::date AS day
            ),
            new_users AS (
                SELECT created_at::date AS day, count(*) AS c FROM users GROUP BY 1
            ),
            msgs AS (
                SELECT created_at::date AS day, count(*) AS c
                FROM messages WHERE deleted_at IS NULL GROUP BY 1
            )
            SELECT to_char(calendar.day, 'YYYY-MM-DD') AS day,
                   coalesce(new_users.c, 0) AS users,
                   coalesce(msgs.c, 0) AS messages
            FROM calendar
            LEFT JOIN new_users ON new_users.day = calendar.day
            LEFT JOIN msgs ON msgs.day = calendar.day
            ORDER BY calendar.day
        """),
        {"days": days},
    )).all()
    return [TimeseriesPoint(date=r[0], users=int(r[1]), messages=int(r[2])) for r in rows]


@router.get("/health", response_model=SystemHealth)
async def system_health(
    db: DbDep,
    runtime: RuntimeDep,
    user: User = Depends(require_permission(Permission.MONITORING_READ)),
):
    database = "ok"
    try:
        await db.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        database = f"error: {exc.__class__.__name__}"

    redis_status = "disabled"
    if runtime.redis is not None:
        try:
            await runtime.redis.ping()
            redis_status = "ok"
        except Exception as exc:  # noqa: BLE001
            redis_status = f"error: {exc.__class__.__name__}"

    memory = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    return SystemHealth(
        database=database,
        redis=redis_status,
        api="ok",
        websocket_connections=runtime.manager.connection_count,
        cpu_percent=psutil.cpu_percent(interval=0.1),
        memory_percent=memory.percent,
        memory_used_bytes=memory.used,
        disk_percent=disk.percent,
        uptime_seconds=int(time.time() - runtime.started_at),
        version=runtime.version,
    )


@router.get("/monitoring/errors")
async def monitoring_errors(
    db: DbDep,
    limit: int = Query(default=100, ge=1, le=500),
    user: User = Depends(require_permission(Permission.MONITORING_READ)),
):
    result = await db.execute(select(ErrorLog).order_by(ErrorLog.created_at.desc()).limit(limit))
    return [
        {
            "id": str(row.id),
            "level": row.level,
            "message": row.message,
            "path": row.path,
            "method": row.method,
            "created_at": row.created_at,
        }
        for row in result.scalars()
    ]


# --------------------------------------------------------------------------- #
# User management
# --------------------------------------------------------------------------- #
@router.get("/users")
async def list_users(
    db: DbDep,
    q: str | None = Query(default=None, max_length=64),
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(require_permission(Permission.USERS_READ)),
):
    stmt = select(User).order_by(User.created_at.desc())
    if q:
        pattern = f"%{q.strip()}%"
        stmt = stmt.where(or_(User.username.ilike(pattern), User.display_name.ilike(pattern)))
    if status_filter == "blocked":
        stmt = stmt.where(User.is_blocked.is_(True))
    elif status_filter == "suspended":
        stmt = stmt.where(User.suspended_until > datetime.now(UTC))
    elif status_filter == "active":
        stmt = stmt.where(User.is_blocked.is_(False), User.is_active.is_(True))

    total = (await db.execute(
        select(func.count()).select_from(stmt.subquery())
    )).scalar_one()
    rows = list((await db.execute(stmt.limit(limit).offset(offset))).scalars())
    return {
        "items": [AdminUserOut(**_user_dict(u)).model_dump() for u in rows],
        "total": int(total),
        "limit": limit,
        "offset": offset,
    }


def _user_dict(u: User) -> dict:
    return {
        "id": u.id,
        "username": u.username,
        "display_name": u.display_name,
        "is_active": u.is_active,
        "is_blocked": u.is_blocked,
        "block_reason": u.block_reason,
        "suspended_until": u.suspended_until,
        "restriction_reason": u.restriction_reason,
        "last_seen_at": u.last_seen_at,
        "created_at": u.created_at,
        "role": u.admin_role.name if u.admin_role else None,
    }


@router.get("/users/{user_id}")
async def user_detail(
    user_id: uuid.UUID,
    db: DbDep,
    user: User = Depends(require_permission(Permission.USERS_READ)),
):
    target = await db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")

    messages_sent = await _scalar(db, "SELECT count(*) FROM messages WHERE sender_id = :uid",
                                  uid=str(user_id))
    chats = await _scalar(db, "SELECT count(*) FROM chat_members WHERE user_id = :uid",
                          uid=str(user_id))
    files = await _scalar(db, "SELECT count(*) FROM files WHERE owner_id = :uid", uid=str(user_id))
    reports_against = await _scalar(
        db, "SELECT count(*) FROM reports WHERE target_type = 'user' AND target_id = :uid",
        uid=str(user_id),
    )
    events = list((await db.execute(
        select(SecurityEvent).where(SecurityEvent.user_id == user_id)
        .order_by(SecurityEvent.created_at.desc()).limit(20)
    )).scalars())

    return {
        "user": AdminUserOut(**_user_dict(target)).model_dump(),
        "stats": {
            "messages_sent": messages_sent,
            "chats": chats,
            "files": files,
            "reports_against": reports_against,
        },
        "security_events": [
            {"type": e.type, "ip": e.ip, "created_at": e.created_at, "meta": e.meta} for e in events
        ],
    }


@router.post("/users/{user_id}/block", status_code=204)
async def block_user(
    user_id: uuid.UUID,
    payload: UserBlock,
    request: Request,
    db: DbDep,
    user: User = Depends(require_permission(Permission.USERS_BLOCK)),
):
    target = await db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")
    if _role_name(target) == "super_admin":
        raise HTTPException(status_code=403, detail="Cannot moderate a super admin")
    target.is_blocked = payload.blocked
    target.block_reason = payload.reason if payload.blocked else None
    await db.commit()
    await audit_log(db, request, user.id, "user.block" if payload.blocked else "user.unblock",
                    "user", str(user_id), {"reason": payload.reason})
    return None


@router.post("/users/{user_id}/suspend", status_code=204)
async def suspend_user(
    user_id: uuid.UUID,
    payload: UserSuspend,
    request: Request,
    db: DbDep,
    user: User = Depends(require_permission(Permission.USERS_WRITE)),
):
    target = await db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")
    if _role_name(target) == "super_admin":
        raise HTTPException(status_code=403, detail="Cannot moderate a super admin")
    target.suspended_until = payload.until
    target.restriction_reason = payload.reason
    await db.commit()
    await audit_log(db, request, user.id, "user.suspend", "user", str(user_id),
                    {"until": payload.until.isoformat() if payload.until else None,
                     "reason": payload.reason})
    return None


@router.post("/users/{user_id}/role", status_code=204)
async def assign_role(
    user_id: uuid.UUID,
    payload: dict,
    request: Request,
    db: DbDep,
    user: User = Depends(require_permission(Permission.ROLES_WRITE)),
):
    role_name = str(payload.get("role", "none"))
    target = await db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")

    if role_name == "none":
        target.admin_role_id = None
    else:
        role = (await db.execute(select(Role).where(Role.name == role_name))).scalar_one_or_none()
        if role is None:
            raise HTTPException(status_code=404, detail="Role not found")
        # Only a super admin may grant super admin.
        if role.name == "super_admin" and _role_name(user) != "super_admin":
            raise HTTPException(status_code=403, detail="Only a super admin can grant this role")
        target.admin_role_id = role.id
    await db.commit()
    await audit_log(db, request, user.id, "user.role_change", "user", str(user_id),
                    {"role": role_name})
    return None


@router.delete("/users/{user_id}", status_code=204)
async def deactivate_user(
    user_id: uuid.UUID,
    request: Request,
    db: DbDep,
    user: User = Depends(require_permission(Permission.USERS_WRITE)),
):
    target = await db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")
    if _role_name(target) == "super_admin":
        raise HTTPException(status_code=403, detail="Cannot deactivate a super admin")
    target.is_active = False
    await db.commit()
    await audit_log(db, request, user.id, "user.deactivate", "user", str(user_id))
    return None


# --------------------------------------------------------------------------- #
# Chats
# --------------------------------------------------------------------------- #
@router.get("/chats")
async def list_chats(
    db: DbDep,
    type_filter: str | None = Query(default=None, alias="type"),
    q: str | None = Query(default=None, max_length=64),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(require_permission(Permission.CHATS_READ)),
):
    stmt = select(Chat).order_by(Chat.created_at.desc())
    if type_filter:
        stmt = stmt.where(Chat.type == type_filter)
    if q:
        pattern = f"%{q.strip()}%"
        stmt = stmt.where(or_(Chat.title.ilike(pattern), Chat.username.ilike(pattern)))

    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = list((await db.execute(stmt.limit(limit).offset(offset))).scalars())
    return {
        "items": [
            {
                "id": str(c.id), "type": c.type, "title": c.title, "username": c.username,
                "member_count": c.member_count, "is_public": c.is_public,
                "created_at": c.created_at, "owner_id": str(c.owner_id) if c.owner_id else None,
            }
            for c in rows
        ],
        "total": int(total),
    }


@router.get("/chats/{chat_id}")
async def chat_detail(
    chat_id: uuid.UUID,
    db: DbDep,
    user: User = Depends(require_permission(Permission.CHATS_READ)),
):
    chat = await db.get(Chat, chat_id)
    if chat is None:
        raise HTTPException(status_code=404, detail="Chat not found")
    members = (await db.execute(
        select(ChatMember, User).join(User, User.id == ChatMember.user_id)
        .where(ChatMember.chat_id == chat_id, ChatMember.left_at.is_(None))
        .order_by(ChatMember.role, User.username)
        .limit(200)
    )).all()
    message_count = await _scalar(db, "SELECT count(*) FROM messages WHERE chat_id = :cid",
                                  cid=str(chat_id))
    return {
        "chat": {
            "id": str(chat.id), "type": chat.type, "title": chat.title,
            "username": chat.username, "about": chat.about,
            "member_count": chat.member_count, "is_public": chat.is_public,
            "created_at": chat.created_at, "last_seq": chat.last_seq,
        },
        "message_count": message_count,
        "members": [
            {"user_id": str(m.user_id), "username": u.username, "display_name": u.display_name,
             "role": m.role, "joined_at": m.joined_at}
            for m, u in members
        ],
    }


@router.delete("/chats/{chat_id}", status_code=204)
async def delete_chat(
    chat_id: uuid.UUID,
    request: Request,
    db: DbDep,
    user: User = Depends(require_permission(Permission.CHATS_WRITE)),
):
    chat = await db.get(Chat, chat_id)
    if chat is None:
        raise HTTPException(status_code=404, detail="Chat not found")
    await db.delete(chat)
    await db.commit()
    await audit_log(db, request, user.id, "chat.delete", "chat", str(chat_id),
                    {"title": chat.title, "type": chat.type})
    return None


# --------------------------------------------------------------------------- #
# Reports
# --------------------------------------------------------------------------- #
@router.get("/reports")
async def list_reports(
    db: DbDep,
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(require_permission(Permission.REPORTS_READ)),
):
    stmt = select(Report).order_by(Report.created_at.desc())
    if status_filter:
        stmt = stmt.where(Report.status == status_filter)
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = list((await db.execute(stmt.limit(limit).offset(offset))).scalars())
    return {"items": [ReportOut.model_validate(r).model_dump() for r in rows], "total": int(total)}


@router.post("/reports/{report_id}/resolve", response_model=ReportOut)
async def resolve_report(
    report_id: uuid.UUID,
    payload: ReportResolve,
    request: Request,
    db: DbDep,
    user: User = Depends(require_permission(Permission.REPORTS_WRITE)),
):
    report = await db.get(Report, report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="Report not found")
    report.status = payload.status
    report.resolution = payload.resolution
    report.handled_by = user.id
    await db.commit()
    await db.refresh(report)
    await audit_log(db, request, user.id, "report.resolve", "report", str(report_id),
                    {"status": payload.status})
    return report


# --------------------------------------------------------------------------- #
# Tickets
# --------------------------------------------------------------------------- #
@router.get("/tickets")
async def list_tickets(
    db: DbDep,
    status_filter: str | None = Query(default=None, alias="status"),
    assigned: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(require_permission(Permission.TICKETS_READ)),
):
    stmt = select(Ticket).order_by(Ticket.created_at.desc())
    if status_filter:
        stmt = stmt.where(Ticket.status == status_filter)
    if assigned == "me":
        stmt = stmt.where(Ticket.assigned_to == user.id)
    elif assigned == "unassigned":
        stmt = stmt.where(Ticket.assigned_to.is_(None))
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = list((await db.execute(stmt.limit(limit).offset(offset))).scalars())
    return {"items": [TicketOut.model_validate(t).model_dump() for t in rows], "total": int(total)}


@router.get("/tickets/{ticket_id}")
async def ticket_detail(
    ticket_id: uuid.UUID,
    db: DbDep,
    user: User = Depends(require_permission(Permission.TICKETS_READ)),
):
    ticket = await db.get(Ticket, ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    messages = list((await db.execute(
        select(TicketMessage).where(TicketMessage.ticket_id == ticket_id)
        .order_by(TicketMessage.created_at)
    )).scalars())
    return {
        "ticket": TicketOut.model_validate(ticket).model_dump(),
        "messages": [TicketMessageOut.model_validate(m).model_dump() for m in messages],
    }


@router.patch("/tickets/{ticket_id}")
async def update_ticket(
    ticket_id: uuid.UUID,
    payload: dict,
    request: Request,
    db: DbDep,
    user: User = Depends(require_permission(Permission.TICKETS_WRITE)),
):
    ticket = await db.get(Ticket, ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket not found")

    changed: dict = {}
    if "assigned_to" in payload:
        value = payload["assigned_to"]
        if value in (None, "none"):
            ticket.assigned_to = None
        else:
            try:
                assignee = uuid.UUID(str(value))
            except ValueError:
                raise HTTPException(status_code=422, detail="invalid assigned_to")
            staff = await db.get(User, assignee)
            if staff is None or staff.admin_role_id is None:
                raise HTTPException(status_code=422, detail="assignee must be a staff member")
            ticket.assigned_to = assignee
        changed["assigned_to"] = payload["assigned_to"]
    if payload.get("priority"):
        if payload["priority"] not in {"low", "normal", "high", "urgent"}:
            raise HTTPException(status_code=422, detail="invalid priority")
        ticket.priority = payload["priority"]
        changed["priority"] = payload["priority"]
    if payload.get("status"):
        if payload["status"] not in {"open", "pending", "resolved", "closed"}:
            raise HTTPException(status_code=422, detail="invalid status")
        ticket.status = payload["status"]
        ticket.closed_at = datetime.now(UTC) if payload["status"] == "closed" else None
        changed["status"] = payload["status"]
    await db.commit()
    await audit_log(db, request, user.id, "ticket.update", "ticket", str(ticket_id), changed)
    await db.refresh(ticket)
    return TicketOut.model_validate(ticket).model_dump()


@router.post("/tickets/{ticket_id}/reply", response_model=TicketMessageOut, status_code=201)
async def staff_reply(
    ticket_id: uuid.UUID,
    payload: TicketMessageCreate,
    request: Request,
    db: DbDep,
    runtime: RuntimeDep,
    user: User = Depends(require_permission(Permission.TICKETS_WRITE)),
):
    ticket = await db.get(Ticket, ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    if ticket.status == "closed":
        raise HTTPException(status_code=409, detail="Ticket is closed")

    message = TicketMessage(ticket_id=ticket_id, author_id=user.id, is_staff=True, body=payload.body)
    db.add(message)
    if ticket.assigned_to is None:
        ticket.assigned_to = user.id
    await db.commit()
    await db.refresh(message)
    await audit_log(db, request, user.id, "ticket.reply", "ticket", str(ticket_id))
    await emit(runtime, [ticket.user_id], "ticket.message",
               {"ticket_id": str(ticket_id), "message_id": str(message.id)})
    return message


@router.get("/tickets-performance")
async def tickets_performance(
    db: DbDep,
    user: User = Depends(require_permission(Permission.TICKETS_READ)),
):
    rows = (await db.execute(
        text("""
            SELECT u.username,
                   count(t.id) AS assigned,
                   count(t.id) FILTER (WHERE t.status IN ('resolved','closed')) AS resolved,
                   count(m.id) AS replies
            FROM users u
            LEFT JOIN tickets t ON t.assigned_to = u.id
            LEFT JOIN ticket_messages m ON m.ticket_id = t.id AND m.is_staff
            WHERE u.admin_role_id IS NOT NULL
            GROUP BY u.username
            ORDER BY resolved DESC
        """)
    )).all()
    return [
        {"username": r[0], "assigned": int(r[1]), "resolved": int(r[2]), "replies": int(r[3])}
        for r in rows
    ]


# --------------------------------------------------------------------------- #
# Roles
# --------------------------------------------------------------------------- #
@router.get("/roles", response_model=list[RoleOut])
async def list_roles(db: DbDep, user: User = Depends(require_permission(Permission.USERS_READ))):
    rows = list((await db.execute(select(Role).order_by(Role.name))).scalars())
    return [RoleOut.model_validate(r) for r in rows]


@router.post("/roles", response_model=RoleOut, status_code=201)
async def create_role(
    payload: RoleCreate,
    request: Request,
    db: DbDep,
    user: User = Depends(require_permission(Permission.ROLES_WRITE)),
):
    unknown = set(payload.permissions) - set(ALL_PERMISSIONS)
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unknown permissions: {sorted(unknown)}")
    existing = (await db.execute(select(Role).where(Role.name == payload.name))).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status_code=409, detail="Role name already exists")
    role = Role(name=payload.name, description=payload.description,
                permissions=sorted(set(payload.permissions)), is_system=False)
    db.add(role)
    await db.commit()
    await db.refresh(role)
    await audit_log(db, request, user.id, "role.create", "role", str(role.id),
                    {"name": role.name, "permissions": role.permissions})
    return RoleOut.model_validate(role)


@router.patch("/roles/{role_id}", response_model=RoleOut)
async def update_role(
    role_id: uuid.UUID,
    payload: RoleUpdate,
    request: Request,
    db: DbDep,
    user: User = Depends(require_permission(Permission.ROLES_WRITE)),
):
    role = await db.get(Role, role_id)
    if role is None:
        raise HTTPException(status_code=404, detail="Role not found")
    if role.is_system and role.name == "super_admin":
        raise HTTPException(status_code=403, detail="super_admin permissions are immutable")
    if payload.permissions is not None:
        unknown = set(payload.permissions) - set(ALL_PERMISSIONS)
        if unknown:
            raise HTTPException(status_code=422, detail=f"Unknown permissions: {sorted(unknown)}")
        role.permissions = sorted(set(payload.permissions))
    if payload.description is not None:
        role.description = payload.description
    await db.commit()
    await db.refresh(role)
    await audit_log(db, request, user.id, "role.update", "role", str(role_id),
                    {"permissions": role.permissions})
    return RoleOut.model_validate(role)


# --------------------------------------------------------------------------- #
# Audit & security
# --------------------------------------------------------------------------- #
@router.get("/audit", response_model=list[AuditOut])
async def list_audit(
    db: DbDep,
    action: str | None = Query(default=None, max_length=64),
    actor_id: uuid.UUID | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(require_permission(Permission.AUDIT_READ)),
):
    stmt = select(AuditLog).order_by(AuditLog.created_at.desc())
    if action:
        stmt = stmt.where(AuditLog.action == action)
    if actor_id:
        stmt = stmt.where(AuditLog.actor_id == actor_id)
    rows = list((await db.execute(stmt.limit(limit).offset(offset))).scalars())
    return [AuditOut.model_validate(r) for r in rows]


@router.get("/security-events")
async def list_security_events(
    db: DbDep,
    type_filter: str | None = Query(default=None, alias="type"),
    limit: int = Query(default=100, ge=1, le=500),
    user: User = Depends(require_permission(Permission.AUDIT_READ)),
):
    stmt = select(SecurityEvent).order_by(SecurityEvent.created_at.desc())
    if type_filter:
        stmt = stmt.where(SecurityEvent.type == type_filter)
    rows = list((await db.execute(stmt.limit(limit))).scalars())
    return [
        {"id": str(r.id), "user_id": str(r.user_id) if r.user_id else None, "type": r.type,
         "ip": r.ip, "user_agent": r.user_agent, "meta": r.meta, "created_at": r.created_at}
        for r in rows
    ]


# --------------------------------------------------------------------------- #
# Platform settings & maintenance
# --------------------------------------------------------------------------- #
DEFAULT_SETTINGS = {
    "registration": {"enabled": True, "require_approval": False},
    "security": {"max_sessions_per_user": 10, "password_min_length": 8},
    "limits": {"max_file_size_bytes": 209715200, "max_message_length": 8192,
               "max_group_members": 5000},
    "notifications": {"push_enabled": True},
    "maintenance": {"enabled": False, "message": "Scheduled maintenance"},
    "app_versions": {"android_min": "1.0.0", "android_latest": "1.0.0"},
    "retention": {"messages_days": 0, "audit_days": 365},
}


@router.get("/settings", response_model=list[SettingOut])
async def list_settings(db: DbDep, user: User = Depends(require_permission(Permission.SETTINGS_WRITE))):
    rows = {row.key: row for row in (await db.execute(select(AppSetting))).scalars()}
    out = []
    for key, default in DEFAULT_SETTINGS.items():
        row = rows.get(key)
        out.append(
            SettingOut(
                key=key,
                value=row.value if row else default,
                updated_at=row.updated_at if row else datetime.now(UTC),
            )
        )
    for key, row in rows.items():
        if key not in DEFAULT_SETTINGS:
            out.append(SettingOut(key=key, value=row.value, updated_at=row.updated_at))
    return out


@router.put("/settings", response_model=list[SettingOut])
async def update_settings(
    payload: SettingsUpdate,
    request: Request,
    db: DbDep,
    user: User = Depends(require_permission(Permission.SETTINGS_WRITE)),
):
    for key, value in payload.values.items():
        row = await db.get(AppSetting, key)
        if row is None:
            db.add(AppSetting(key=key, value=value, updated_by=user.id))
        else:
            row.value = value
            row.updated_by = user.id
    await db.commit()
    await audit_log(db, request, user.id, "settings.update", "settings", None,
                    {"keys": sorted(payload.values)})
    return await list_settings(db, user)


@router.post("/maintenance", status_code=204)
async def set_maintenance(
    payload: dict,
    request: Request,
    db: DbDep,
    user: User = Depends(require_permission(Permission.SETTINGS_WRITE)),
):
    enabled = bool(payload.get("enabled"))
    message = str(payload.get("message", "Scheduled maintenance"))[:255]
    row = await db.get(AppSetting, "maintenance")
    if row is None:
        db.add(AppSetting(key="maintenance", value={"enabled": enabled, "message": message},
                          updated_by=user.id))
    else:
        row.value = {"enabled": enabled, "message": message}
        row.updated_by = user.id
    await db.commit()
    cache = getattr(request.app.state, "maintenance_cache", None)
    if cache is not None:
        cache.update(checked_at=time.time(), enabled=enabled, message=message)
    await audit_log(db, request, user.id, "maintenance.toggle", "settings", "maintenance",
                    {"enabled": enabled})
    return None


# --------------------------------------------------------------------------- #
# Exports
# --------------------------------------------------------------------------- #
EXPORT_QUERIES: dict[str, str] = {
    "users": "SELECT id, username, display_name, is_active, is_blocked, created_at, last_seen_at FROM users ORDER BY created_at DESC LIMIT 100000",
    "messages": "SELECT id, chat_id, sender_id, seq, type, created_at FROM messages WHERE deleted_at IS NULL ORDER BY created_at DESC LIMIT 100000",
    "chats": "SELECT id, type, title, username, member_count, created_at FROM chats ORDER BY created_at DESC LIMIT 100000",
    "reports": "SELECT id, target_type, target_id, reason, status, created_at FROM reports ORDER BY created_at DESC LIMIT 100000",
    "tickets": "SELECT id, user_id, subject, category, priority, status, assigned_to, created_at FROM tickets ORDER BY created_at DESC LIMIT 100000",
    "calls": "SELECT id, chat_id, initiator_id, kind, status, created_at FROM calls ORDER BY created_at DESC LIMIT 100000",
}


@router.get("/export/{dataset}.csv")
async def export_csv(
    dataset: str,
    request: Request,
    db: DbDep,
    user: User = Depends(require_permission(Permission.EXPORT_DATA)),
):
    sql = EXPORT_QUERIES.get(dataset)
    if sql is None:
        raise HTTPException(status_code=404, detail=f"Unknown dataset. Use one of {sorted(EXPORT_QUERIES)}")

    result = await db.execute(text(sql))
    columns = list(result.keys())
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(columns)
    for row in result.all():
        writer.writerow(["" if value is None else value for value in row])
    buffer.seek(0)
    await audit_log(db, request, user.id, "export.csv", "dataset", dataset)
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{dataset}.csv"'},
    )


@router.get("/export/summary.pdf")
async def export_pdf(
    request: Request,
    db: DbDep,
    user: User = Depends(require_permission(Permission.EXPORT_DATA)),
):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, title="Platform report")
    styles = getSampleStyleSheet()
    story = [Paragraph("Platform summary report", styles["Title"]),
             Paragraph(f"Generated {datetime.now(UTC).isoformat(timespec='seconds')} by "
                       f"{user.username}", styles["Normal"]),
             Spacer(1, 18)]

    metrics = [
        ("Total users", await _scalar(db, "SELECT count(*) FROM users")),
        ("Active today", await _scalar(
            db, "SELECT count(*) FROM users WHERE last_seen_at >= date_trunc('day', now())")),
        ("Messages", await _scalar(db, "SELECT count(*) FROM messages WHERE deleted_at IS NULL")),
        ("Groups", await _scalar(db, "SELECT count(*) FROM chats WHERE type = 'group'")),
        ("Channels", await _scalar(db, "SELECT count(*) FROM chats WHERE type = 'channel'")),
        ("Files", await _scalar(db, "SELECT count(*) FROM files")),
        ("Open reports", await _scalar(
            db, "SELECT count(*) FROM reports WHERE status IN ('open','reviewing')")),
        ("Open tickets", await _scalar(
            db, "SELECT count(*) FROM tickets WHERE status IN ('open','pending')")),
    ]
    table = Table([("Metric", "Value"), *[(label, str(value)) for label, value in metrics]],
                  colWidths=[260, 160])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2AABEE")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F2F4F7")]),
        ("PADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(table)
    doc.build(story)
    await audit_log(db, request, user.id, "export.pdf", "report", "summary")
    buffer.seek(0)
    return Response(
        content=buffer.getvalue(),
        media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="platform-summary.pdf"'},
    )
