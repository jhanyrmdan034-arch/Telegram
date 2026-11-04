"""File upload and authenticated download."""
from __future__ import annotations

import uuid
from typing import Literal

from fastapi import (APIRouter, File as FormFile, Form, HTTPException, Request,
                     Response, UploadFile, status)
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from ..deps import CurrentUser, DbDep, RuntimeDep
from ..models import ChatMember, File, Message
from ..schemas import FileOut
from ..storage import iter_file

router = APIRouter(prefix="/files", tags=["files"])

ALLOWED_KINDS = {"photo", "video", "document", "voice", "sticker", "avatar", "other"}


@router.post("", response_model=FileOut, status_code=status.HTTP_201_CREATED)
async def upload_file(
    user: CurrentUser,
    db: DbDep,
    runtime: RuntimeDep,
    file: UploadFile = FormFile(...),
    kind: str = Form(default="document"),
    is_public: bool = Form(default=False),
):
    if kind not in ALLOWED_KINDS:
        raise HTTPException(status_code=422, detail=f"kind must be one of {sorted(ALLOWED_KINDS)}")

    stored = await runtime.storage.save(file, runtime.settings.max_file_size_bytes)
    record = File(
        owner_id=user.id,
        storage_key=stored.storage_key,
        name=(file.filename or "file")[:255],
        mime_type=(file.content_type or "application/octet-stream")[:128],
        size=stored.size,
        sha256=stored.sha256,
        kind=kind,
        is_public=is_public,
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)
    return _file_out(record, runtime)


def _file_out(record: File, runtime) -> FileOut:
    out = FileOut.model_validate(record)
    out.download_url = f"{runtime.settings.public_base_url}/api/v1/files/{record.id}/content"
    return out


async def _can_access(db, file_record: File, user_id: uuid.UUID) -> bool:
    if file_record.is_public or file_record.owner_id == user_id:
        return True
    # Otherwise the requester must be a member of a chat where the file is used.
    result = await db.execute(
        select(Message.id)
        .join(ChatMember, ChatMember.chat_id == Message.chat_id)
        .where(
            Message.file_id == file_record.id,
            Message.deleted_at.is_(None),
            ChatMember.user_id == user_id,
            ChatMember.left_at.is_(None),
        )
        .limit(1)
    )
    return result.scalar_one_or_none() is not None


@router.get("/{file_id}", response_model=FileOut)
async def file_metadata(file_id: uuid.UUID, user: CurrentUser, db: DbDep, runtime: RuntimeDep):
    record = await db.get(File, file_id)
    if record is None:
        raise HTTPException(status_code=404, detail="File not found")
    if not await _can_access(db, record, user.id):
        raise HTTPException(status_code=403, detail="Access denied")
    return _file_out(record, runtime)


@router.get("/{file_id}/content")
async def download_file(file_id: uuid.UUID, request: Request, user: CurrentUser,
                        db: DbDep, runtime: RuntimeDep):
    record = await db.get(File, file_id)
    if record is None:
        raise HTTPException(status_code=404, detail="File not found")
    if not await _can_access(db, record, user.id):
        raise HTTPException(status_code=403, detail="Access denied")

    etag = f'"{record.sha256}"'
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=status.HTTP_304_NOT_MODIFIED,
                        headers={"ETag": etag, "Cache-Control": "private, max-age=0, must-revalidate"})

    path = runtime.storage.path_for(record.storage_key)
    if not path.exists():
        raise HTTPException(status_code=410, detail="Stored object is missing")
    return StreamingResponse(
        iter_file(path),
        media_type=record.mime_type,
        headers={
            "Content-Length": str(record.size),
            "Content-Disposition": f'inline; filename="{record.name}"',
            # The digest is the version: a re-upload is a new file, a new sha256
            # and therefore a new ETag, so clients can revalidate instead of
            # re-downloading avatars on every list refresh.
            "ETag": etag,
            "Cache-Control": "private, max-age=0, must-revalidate",
        },
    )


@router.delete("/{file_id}", status_code=204)
async def delete_file(file_id: uuid.UUID, user: CurrentUser, db: DbDep, runtime: RuntimeDep):
    record = await db.get(File, file_id)
    if record is None:
        raise HTTPException(status_code=404, detail="File not found")
    if record.owner_id != user.id:
        raise HTTPException(status_code=403, detail="Only the owner can delete this file")
    await runtime.storage.delete(record.storage_key)
    await db.delete(record)
    await db.commit()
    return None
