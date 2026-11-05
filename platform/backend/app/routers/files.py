"""File upload and authenticated download."""
from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Literal

from fastapi import (APIRouter, File as FormFile, Form, HTTPException, Request,
                     Response, UploadFile, status)
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import select

from ..deps import CurrentUser, DbDep, RuntimeDep
from ..media import HEAD_BYTES, safe_filename, validate
from ..models import ChatMember, File, Message
from ..schemas import FileOut, MultipartInit, MultipartOut
from ..services import emit, member_ids
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

    sniffed: dict[str, str] = {}

    def head_check(head: bytes) -> None:
        try:
            sniffed["mime"] = validate(kind, head, file.content_type).mime
        except ValueError as error:
            # Nothing is committed yet: the temp file is discarded by save().
            raise HTTPException(status_code=415, detail=str(error)) from error

    stored = await runtime.storage.save(file, runtime.settings.max_file_size_bytes, head_check)

    # Same bytes, same owner, same kind: reuse the row instead of duplicating it.
    existing = (await db.execute(
        select(File).where(File.owner_id == user.id, File.sha256 == stored.sha256,
                           File.kind == kind)
    )).scalars().first()
    if existing is not None:
        return _file_out(existing, runtime)

    record = File(
        owner_id=user.id,
        storage_key=stored.storage_key,
        name=safe_filename(file.filename or "file"),
        # The sniffed type is authoritative, never the client's claim.
        mime_type=sniffed.get("mime", "application/octet-stream")[:128],
        size=stored.size,
        sha256=stored.sha256,
        kind=kind,
        is_public=is_public,
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)
    await emit(runtime, [user.id], "media.uploaded", {
        "file_id": str(record.id), "kind": record.kind, "mime_type": record.mime_type,
        "size": record.size, "sha256": record.sha256, "name": record.name,
    })
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
        await _denied(db, runtime, user.id, record, "file.download_denied")
        raise HTTPException(status_code=403, detail="Access denied")
    return _file_out(record, runtime)


@router.get("/{file_id}/content")
async def download_file(file_id: uuid.UUID, request: Request, user: CurrentUser,
                        db: DbDep, runtime: RuntimeDep):
    record = await db.get(File, file_id)
    if record is None:
        raise HTTPException(status_code=404, detail="File not found")
    if not await _can_access(db, record, user.id):
        await _denied(db, runtime, user.id, record, "file.download_denied")
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
            "Content-Disposition": f'inline; filename="{safe_filename(record.name)}"',
            # The digest is the version: a re-upload is a new file, a new sha256
            # and therefore a new ETag, so clients can revalidate instead of
            # re-downloading avatars on every list refresh.
            "ETag": etag,
            "Cache-Control": "private, max-age=0, must-revalidate",
        },
    )


async def _denied(db, runtime, user_id: uuid.UUID, record: File, event_type: str) -> None:
    """Security-relevant refusals are recorded with the actor, never the path."""
    try:
        await emit(runtime, [user_id], event_type, {
            "file_id": str(record.id), "owner_id": str(record.owner_id) if record.owner_id else None,
        })
    except Exception:  # pragma: no cover - never fail a request because of telemetry
        pass


async def _users_of_file_chats(db, file_id: uuid.UUID) -> list[uuid.UUID]:
    rows = (await db.execute(
        select(ChatMember.user_id)
        .where(
            ChatMember.chat_id.in_(select(Message.chat_id).where(Message.file_id == file_id)),
            ChatMember.left_at.is_(None),
        ).distinct()
    )).scalars().all()
    return list(rows)


@router.delete("/{file_id}", status_code=204)
async def delete_file(file_id: uuid.UUID, user: CurrentUser, db: DbDep, runtime: RuntimeDep):
    record = await db.get(File, file_id)
    if record is None:
        raise HTTPException(status_code=404, detail="File not found")
    if record.owner_id != user.id:
        raise HTTPException(status_code=403, detail="Only the owner can delete this file")
    audience = await _users_of_file_chats(db, record.id)
    await runtime.storage.delete(record.storage_key)
    await db.delete(record)
    await db.commit()
    if audience:
        await emit(runtime, audience, "media.deleted", {"file_id": str(record.id)})
    return None


# --------------------------------------------------------------------------- #
# Multipart upload: large files in bounded pieces, owner-only completion
# --------------------------------------------------------------------------- #

MULTIPART_PART_SIZE = 8 * 1024 * 1024
MULTIPART_MAX_PARTS = 1000
MULTIPART_DIR = "._multipart"


def _multipart_root(runtime) -> Path:
    root = runtime.storage.path_for("_").parent.parent / MULTIPART_DIR
    root.mkdir(parents=True, exist_ok=True)
    return root


def _multipart_dir(runtime, upload_id: str) -> Path:
    directory = _multipart_root(runtime) / upload_id
    directory.mkdir(parents=True, exist_ok=True)
    return directory


@router.post("/multipart/init", response_model=MultipartOut, status_code=201)
async def multipart_init(payload: MultipartInit, user: CurrentUser, runtime: RuntimeDep):
    if payload.size > runtime.settings.max_file_size_bytes:
        raise HTTPException(status_code=413,
                            detail=f"File exceeds the {runtime.settings.max_file_size_bytes} byte limit")
    if payload.size > MULTIPART_PART_SIZE * MULTIPART_MAX_PARTS:
        raise HTTPException(status_code=413, detail="File needs too many parts")
    for kind in (payload.kind,):
        if kind not in ALLOWED_KINDS:
            raise HTTPException(status_code=422, detail=f"kind must be one of {sorted(ALLOWED_KINDS)}")
    upload_id = uuid.uuid4().hex
    directory = _multipart_dir(runtime, upload_id)
    (directory / "meta.json").write_text(json.dumps({
        "owner_id": str(user.id), "name": safe_filename(payload.name), "kind": payload.kind,
        "size": payload.size, "mime_type": payload.mime_type,
        "part_size": MULTIPART_PART_SIZE,
    }))
    return MultipartOut(upload_id=upload_id, part_size=MULTIPART_PART_SIZE,
                        max_parts=MULTIPART_MAX_PARTS)


def _load_meta(runtime, upload_id: str, user_id: uuid.UUID) -> dict:
    if not upload_id.isalnum():
        raise HTTPException(status_code=404, detail="Upload not found")
    meta_path = _multipart_root(runtime) / upload_id / "meta.json"
    if not meta_path.exists():
        raise HTTPException(status_code=404, detail="Upload not found")
    meta = json.loads(meta_path.read_text())
    if meta["owner_id"] != str(user_id):
        # Another account may never touch this upload: no IDOR by guessing ids.
        raise HTTPException(status_code=403, detail="Upload belongs to another account")
    return meta


@router.put("/multipart/{upload_id}/parts/{index}", response_model=dict)
async def multipart_part(upload_id: str, index: int, request: Request, user: CurrentUser,
                         runtime: RuntimeDep):
    meta = _load_meta(runtime, upload_id, user.id)
    if index < 0 or index >= MULTIPART_MAX_PARTS:
        raise HTTPException(status_code=422, detail="Part index out of range")
    body = await request.body()
    if len(body) > meta["part_size"]:
        raise HTTPException(status_code=413, detail="Part is larger than part_size")
    if not body:
        raise HTTPException(status_code=400, detail="Empty part")
    directory = _multipart_dir(runtime, upload_id)
    (directory / f"{index:05d}.part").write_bytes(body)
    received = sorted(p.name for p in directory.glob("*.part"))
    return {"received_parts": len(received), "next_index": len(received)}


@router.post("/multipart/{upload_id}/complete", response_model=FileOut, status_code=201)
async def multipart_complete(upload_id: str, user: CurrentUser, db: DbDep, runtime: RuntimeDep):
    meta = _load_meta(runtime, upload_id, user.id)
    directory = _multipart_root(runtime) / upload_id
    parts = sorted(directory.glob("*.part"))
    if not parts:
        raise HTTPException(status_code=400, detail="No parts were uploaded")
    assembled = directory / "assembled.bin"
    total = 0
    with assembled.open("wb") as handle:
        for part in parts:
            data = part.read_bytes()
            total += len(data)
            handle.write(data)
    if total != meta["size"]:
        assembled.unlink(missing_ok=True)
        raise HTTPException(status_code=400,
                            detail=f"Assembled size {total} does not match declared {meta['size']}")

    sniffed: dict[str, str] = {}

    def head_check(head: bytes) -> None:
        try:
            sniffed["mime"] = validate(meta["kind"], head, meta.get("mime_type")).mime
        except ValueError as error:
            raise HTTPException(status_code=415, detail=str(error)) from error

    stored = await runtime.storage.save_temp(assembled, runtime.settings.max_file_size_bytes,
                                             head_check)

    existing = (await db.execute(
        select(File).where(File.owner_id == user.id, File.sha256 == stored.sha256,
                           File.kind == meta["kind"])
    )).scalars().first()
    if existing is not None:
        await runtime.storage.delete(stored.storage_key)
        _cleanup_multipart(directory)
        return _file_out(existing, runtime)

    record = File(owner_id=user.id, storage_key=stored.storage_key, name=meta["name"],
                  mime_type=sniffed.get("mime", "application/octet-stream"), size=stored.size,
                  sha256=stored.sha256, kind=meta["kind"], is_public=False)
    db.add(record)
    await db.commit()
    await db.refresh(record)
    _cleanup_multipart(directory)
    await emit(runtime, [user.id], "media.uploaded", {
        "file_id": str(record.id), "kind": record.kind, "mime_type": record.mime_type,
        "size": record.size, "sha256": record.sha256, "name": record.name,
    })
    return _file_out(record, runtime)


def _cleanup_multipart(directory: Path) -> None:
    for path in directory.glob("*"):
        path.unlink(missing_ok=True)
    directory.rmdir()
