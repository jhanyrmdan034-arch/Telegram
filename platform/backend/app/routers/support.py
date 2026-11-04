"""User-facing abuse reports and support tickets."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select

from ..deps import CurrentUser, DbDep, RuntimeDep, permissions_of
from ..models import Report, Ticket, TicketMessage
from ..schemas import (
    ReportCreate,
    ReportOut,
    TicketAssign,
    TicketCreate,
    TicketMessageCreate,
    TicketMessageOut,
    TicketOut,
)

router = APIRouter(tags=["support"])


@router.post("/reports", response_model=ReportOut, status_code=201)
async def create_report(payload: ReportCreate, user: CurrentUser, db: DbDep):
    report = Report(
        reporter_id=user.id,
        target_type=payload.target_type,
        target_id=payload.target_id,
        reason=payload.reason,
        description=payload.description,
    )
    db.add(report)
    await db.commit()
    await db.refresh(report)
    return report


@router.get("/reports/mine", response_model=list[ReportOut])
async def my_reports(user: CurrentUser, db: DbDep, limit: int = Query(default=50, ge=1, le=200)):
    result = await db.execute(
        select(Report).where(Report.reporter_id == user.id).order_by(Report.created_at.desc()).limit(limit)
    )
    return list(result.scalars())


@router.post("/tickets", response_model=TicketOut, status_code=status.HTTP_201_CREATED)
async def create_ticket(payload: TicketCreate, user: CurrentUser, db: DbDep):
    ticket = Ticket(
        user_id=user.id,
        subject=payload.subject,
        category=payload.category,
        priority=payload.priority,
    )
    db.add(ticket)
    await db.flush()
    db.add(TicketMessage(ticket_id=ticket.id, author_id=user.id, is_staff=False, body=payload.body))
    await db.commit()
    await db.refresh(ticket)
    return ticket


@router.get("/tickets/mine", response_model=list[TicketOut])
async def my_tickets(user: CurrentUser, db: DbDep, limit: int = Query(default=50, ge=1, le=200)):
    result = await db.execute(
        select(Ticket).where(Ticket.user_id == user.id).order_by(Ticket.created_at.desc()).limit(limit)
    )
    return list(result.scalars())


async def _load_ticket(db, ticket_id: uuid.UUID) -> Ticket:
    ticket = await db.get(Ticket, ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return ticket


@router.get("/tickets/{ticket_id}", response_model=TicketOut)
async def get_ticket(ticket_id: uuid.UUID, user: CurrentUser, db: DbDep):
    ticket = await _load_ticket(db, ticket_id)
    if ticket.user_id != user.id and permissions_of(user).isdisjoint({"tickets.read"}):
        raise HTTPException(status_code=403, detail="Access denied")
    return ticket


@router.get("/tickets/{ticket_id}/messages", response_model=list[TicketMessageOut])
async def ticket_messages(ticket_id: uuid.UUID, user: CurrentUser, db: DbDep):
    ticket = await _load_ticket(db, ticket_id)
    staff = "tickets.read" in permissions_of(user)
    if ticket.user_id != user.id and not staff:
        raise HTTPException(status_code=403, detail="Access denied")
    result = await db.execute(
        select(TicketMessage).where(TicketMessage.ticket_id == ticket_id).order_by(TicketMessage.created_at)
    )
    return list(result.scalars())


@router.post("/tickets/{ticket_id}/messages", response_model=TicketMessageOut, status_code=201)
async def reply_ticket(ticket_id: uuid.UUID, payload: TicketMessageCreate, user: CurrentUser,
                       db: DbDep, runtime: RuntimeDep):
    ticket = await _load_ticket(db, ticket_id)
    staff = "tickets.write" in permissions_of(user)
    if ticket.user_id != user.id and not staff:
        raise HTTPException(status_code=403, detail="Access denied")
    if ticket.status == "closed":
        raise HTTPException(status_code=409, detail="Ticket is closed")

    message = TicketMessage(ticket_id=ticket_id, author_id=user.id, is_staff=staff, body=payload.body)
    db.add(message)
    if staff and ticket.assigned_to is None:
        ticket.assigned_to = user.id
    await db.commit()
    await db.refresh(message)

    # Notify the other side of the conversation in real time.
    from ..services import emit

    recipients = [ticket.assigned_to] if staff else ([ticket.assigned_to] if ticket.assigned_to else [])
    await emit(runtime, [uid for uid in recipients if uid and uid != user.id], "ticket.message", {
        "ticket_id": str(ticket_id), "message_id": str(message.id),
    })
    return message
