"""Contact list management."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import delete, select

from ..deps import CurrentUser, DbDep
from ..models import Contact, User
from ..schemas import ContactCreate, ContactOut

router = APIRouter(prefix="/contacts", tags=["contacts"])


@router.get("", response_model=list[ContactOut])
async def list_contacts(user: CurrentUser, db: DbDep, q: str | None = Query(default=None, max_length=64)):
    stmt = (
        select(Contact, User)
        .join(User, User.id == Contact.contact_id)
        .where(Contact.owner_id == user.id)
        .order_by(User.display_name)
    )
    if q:
        pattern = f"%{q.strip()}%"
        stmt = stmt.where(User.username.ilike(pattern) | User.display_name.ilike(pattern))
    result = await db.execute(stmt)
    return [
        ContactOut(
            contact_id=contact.contact_id,
            first_name=contact.first_name,
            last_name=contact.last_name,
            username=other.username,
            display_name=other.display_name,
            created_at=contact.created_at,
        )
        for contact, other in result.all()
    ]


@router.post("", response_model=ContactOut, status_code=201)
async def add_contact(payload: ContactCreate, user: CurrentUser, db: DbDep):
    if payload.contact_id == user.id:
        raise HTTPException(status_code=400, detail="Cannot add yourself")
    other = await db.get(User, payload.contact_id)
    if other is None or not other.is_active:
        raise HTTPException(status_code=404, detail="User not found")

    existing = await db.execute(
        select(Contact).where(Contact.owner_id == user.id, Contact.contact_id == payload.contact_id)
    )
    contact = existing.scalar_one_or_none()
    if contact is None:
        contact = Contact(
            owner_id=user.id,
            contact_id=payload.contact_id,
            first_name=payload.first_name,
            last_name=payload.last_name,
        )
        db.add(contact)
    else:
        contact.first_name = payload.first_name or contact.first_name
        contact.last_name = payload.last_name or contact.last_name
    await db.commit()
    await db.refresh(contact)
    return ContactOut(
        contact_id=contact.contact_id,
        first_name=contact.first_name,
        last_name=contact.last_name,
        username=other.username,
        display_name=other.display_name,
        created_at=contact.created_at,
    )


@router.delete("/{contact_id}", status_code=204)
async def remove_contact(contact_id: uuid.UUID, user: CurrentUser, db: DbDep):
    await db.execute(
        delete(Contact).where(Contact.owner_id == user.id, Contact.contact_id == contact_id)
    )
    await db.commit()
    return None
