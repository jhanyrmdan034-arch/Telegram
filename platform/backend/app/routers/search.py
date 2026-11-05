"""One query, two result kinds: users the viewer may see and chats they belong to."""
from __future__ import annotations

from fastapi import APIRouter, Query
from sqlalchemy import or_, select

from ..deps import CurrentUser, DbDep, RuntimeDep
from ..models import Chat, ChatMember, User
from ..schemas import GlobalSearchResult
from ..services import is_contact, user_public
from .chats import enrich_chats

router = APIRouter(prefix="/search", tags=["search"])


@router.get("", response_model=GlobalSearchResult)
async def global_search(
    user: CurrentUser,
    db: DbDep,
    runtime: RuntimeDep,
    q: str = Query(min_length=1, max_length=64),
    limit: int = Query(default=20, ge=1, le=50),
):
    pattern = f"%{q.strip()}%"
    online = set(runtime.manager.online_user_ids())

    users = []
    rows = (await db.execute(
        select(User).where(
            User.id != user.id,
            User.is_active.is_(True),
            User.search_privacy != "nobody",
            or_(User.username.ilike(pattern), User.display_name.ilike(pattern)),
        ).order_by(User.username).limit(limit)
    )).scalars().all()
    for candidate in rows:
        if candidate.search_privacy == "contacts" and not (
                await is_contact(db, candidate.id, user.id)
                or await is_contact(db, user.id, candidate.id)):
            continue
        users.append(await user_public(db, candidate, user.id, online_ids=online))

    chat_rows = (await db.execute(
        select(Chat, ChatMember)
        .join(ChatMember, ChatMember.chat_id == Chat.id)
        .where(
            ChatMember.user_id == user.id,
            ChatMember.left_at.is_(None),
            ChatMember.archived.is_(False),
            or_(Chat.title.ilike(pattern), Chat.username.ilike(pattern)),
        )
        .order_by(Chat.updated_at.desc())
        .limit(limit)
    )).all()
    chats = await enrich_chats(db, runtime, list(chat_rows), user.id)

    return {"query": q, "users": users, "chats": chats}
