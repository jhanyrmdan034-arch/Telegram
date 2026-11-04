"""Content-addressed file storage.

Local disk is the default backend. The interface is intentionally narrow so an
S3/MinIO implementation can be dropped in for multi-node deployments without
touching the API layer (see docs/ARCHITECTURE-fa.md).
"""
from __future__ import annotations

import hashlib
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator, BinaryIO, Protocol

from fastapi import HTTPException, UploadFile, status

CHUNK = 1024 * 1024


@dataclass
class StoredObject:
    storage_key: str
    size: int
    sha256: str


class Storage(Protocol):
    async def save(self, file: UploadFile, max_size: int) -> StoredObject: ...
    def path_for(self, storage_key: str) -> Path: ...
    def open(self, storage_key: str) -> BinaryIO: ...
    async def delete(self, storage_key: str) -> None: ...
    def total_bytes(self) -> int: ...


class LocalStorage:
    backend_name = "local"

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _target(self, digest: str) -> Path:
        directory = self.root / digest[:2] / digest[2:4]
        directory.mkdir(parents=True, exist_ok=True)
        return directory / digest

    async def save(self, file: UploadFile, max_size: int) -> StoredObject:
        hasher = hashlib.sha256()
        tmp = self.root / f".upload-{uuid.uuid4().hex}"
        size = 0
        try:
            with tmp.open("wb") as handle:
                while chunk := await file.read(CHUNK):
                    size += len(chunk)
                    if size > max_size:
                        raise HTTPException(
                            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                            detail=f"File exceeds the {max_size} byte limit",
                        )
                    hasher.update(chunk)
                    handle.write(chunk)
            if size == 0:
                raise HTTPException(status_code=400, detail="Empty file")
            digest = hasher.hexdigest()
            target = self._target(digest)
            os.replace(tmp, target)
            return StoredObject(storage_key=digest, size=size, sha256=digest)
        finally:
            if tmp.exists():
                tmp.unlink()

    def path_for(self, storage_key: str) -> Path:
        if "/" in storage_key or "\\" in storage_key or ".." in storage_key:
            raise HTTPException(status_code=400, detail="Invalid storage key")
        return self.root / storage_key[:2] / storage_key[2:4] / storage_key

    def open(self, storage_key: str) -> BinaryIO:
        path = self.path_for(storage_key)
        if not path.exists():
            raise HTTPException(status_code=404, detail="Stored object not found")
        return path.open("rb")

    async def delete(self, storage_key: str) -> None:
        path = self.path_for(storage_key)
        if path.exists():
            path.unlink()

    def total_bytes(self) -> int:
        total = 0
        for dirpath, _dirnames, filenames in os.walk(self.root):
            for name in filenames:
                try:
                    total += os.path.getsize(os.path.join(dirpath, name))
                except OSError:
                    continue
        return total


async def iter_file(path: Path, chunk: int = CHUNK) -> AsyncIterator[bytes]:
    with path.open("rb") as handle:
        while data := handle.read(chunk):
            yield data
