"""Chat folders — the client's "folders" screen, backed by real rows."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import delete, select

from ..deps import CurrentUser, DbDep, RuntimeDep
from ..models import Chat, ChatFolder, ChatFolderChat, ChatMember
from ..schemas import ChatOut, FolderChatsUpdate, FolderCreate, FolderOut, FolderUpdate
from ..services import chat_payload

router = APIRouter(prefix="/folders", tags=["folders"])


async def _load_folder(db, folder_id: uuid.UUID, user_id: uuid.UUID) -> ChatFolder:
    folder = await db.get(ChatFolder, folder_id)
    if folder is None or folder.user_id != user_id:
        raise HTTPException(status_code=404, detail="Folder not found")
    return folder


async def _folder_out(db, folder: ChatFolder) -> FolderOut:
    chat_ids = (await db.execute(
        select(ChatFolderChat.chat_id).where(ChatFolderChat.folder_id == folder.id)
    )).scalars().all()
    return FolderOut(
        id=folder.id, name=folder.name, position=folder.position,
        created_at=folder.created_at, chat_ids=list(chat_ids),
    )


@router.get("", response_model=list[FolderOut])
async def list_folders(user: CurrentUser, db: DbDep):
    folders = (await db.execute(
        select(ChatFolder).where(ChatFolder.user_id == user.id)
        .order_by(ChatFolder.position, ChatFolder.created_at)
    )).scalars().all()
    return [await _folder_out(db, folder) for folder in folders]


@router.post("", response_model=FolderOut, status_code=status.HTTP_201_CREATED)
async def create_folder(payload: FolderCreate, user: CurrentUser, db: DbDep):
    existing = (await db.execute(
        select(ChatFolder).where(ChatFolder.user_id == user.id, ChatFolder.name == payload.name)
    )).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status_code=409, detail="A folder with this name already exists")
    folder = ChatFolder(user_id=user.id, name=payload.name, position=payload.position)
    db.add(folder)
    await db.commit()
    await db.refresh(folder)
    return await _folder_out(db, folder)


@router.patch("/{folder_id}", response_model=FolderOut)
async def update_folder(folder_id: uuid.UUID, payload: FolderUpdate, user: CurrentUser, db: DbDep):
    folder = await _load_folder(db, folder_id, user.id)
    if payload.name is not None:
        clash = (await db.execute(
            select(ChatFolder.id).where(
                ChatFolder.user_id == user.id, ChatFolder.name == payload.name,
                ChatFolder.id != folder_id,
            )
        )).scalar_one_or_none()
        if clash is not None:
            raise HTTPException(status_code=409, detail="A folder with this name already exists")
        folder.name = payload.name
    if payload.position is not None:
        folder.position = payload.position
    await db.commit()
    await db.refresh(folder)
    return await _folder_out(db, folder)


@router.delete("/{folder_id}", status_code=204)
async def delete_folder(folder_id: uuid.UUID, user: CurrentUser, db: DbDep):
    folder = await _load_folder(db, folder_id, user.id)
    await db.delete(folder)
    await db.commit()
    return None


@router.put("/{folder_id}/chats", response_model=FolderOut)
async def set_folder_chats(folder_id: uuid.UUID, payload: FolderChatsUpdate, user: CurrentUser,
                           db: DbDep):
    folder = await _load_folder(db, folder_id, user.id)

    # Only chats the user actually belongs to may be added.
    if payload.chat_ids:
        member_chats = set((await db.execute(
            select(ChatMember.chat_id).where(
                ChatMember.user_id == user.id, ChatMember.chat_id.in_(payload.chat_ids),
                ChatMember.left_at.is_(None),
            )
        )).scalars().all())
        missing = set(payload.chat_ids) - member_chats
        if missing:
            raise HTTPException(status_code=404, detail="Unknown or inaccessible chats")

    await db.execute(delete(ChatFolderChat).where(ChatFolderChat.folder_id == folder_id))
    for chat_id in dict.fromkeys(payload.chat_ids):
        db.add(ChatFolderChat(folder_id=folder_id, chat_id=chat_id))
    await db.commit()
    return await _folder_out(db, folder)


@router.get("/{folder_id}/chats", response_model=list[ChatOut])
async def folder_chats(folder_id: uuid.UUID, user: CurrentUser, db: DbDep, runtime: RuntimeDep):
    from ..services import bulk_last_messages, bulk_private_peers

    folder = await _load_folder(db, folder_id, user.id)
    rows = (await db.execute(
        select(Chat, ChatMember)
        .join(ChatFolderChat, ChatFolderChat.chat_id == Chat.id)
        .join(ChatMember, (ChatMember.chat_id == Chat.id) & (ChatMember.user_id == user.id))
        .where(ChatFolderChat.folder_id == folder.id, ChatMember.left_at.is_(None))
        .order_by(ChatMember.is_pinned.desc(), Chat.updated_at.desc())
    )).all()

    chat_ids = [chat.id for chat, _ in rows]
    last_messages = await bulk_last_messages(db, chat_ids)
    peers = await bulk_private_peers(db, chat_ids, user.id)
    online = {str(uid) for uid in runtime.manager.online_user_ids()}

    out = []
    for chat, member in rows:
        peer = peers.get(chat.id)
        if peer is not None:
            peer = {**peer, "is_online": peer["id"] in online}
        out.append(ChatOut(**chat_payload(chat, member, last_message=last_messages.get(chat.id),
                                          peer=peer)))
    return out
