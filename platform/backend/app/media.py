"""Media validation: what a file *is* matters more than what it claims to be.

The uploader's Content-Type and filename are untrusted. Every upload is sniffed
from its magic bytes and checked against the allowlist of the declared kind, so a
renamed executable cannot masquerade as a photo, and the stored mime type is the
sniffed one.
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass

HEAD_BYTES = 64

IMAGE_MIMES = {"image/jpeg", "image/png", "image/gif", "image/webp", "image/heic"}
VIDEO_MIMES = {"video/mp4", "video/webm", "video/quicktime", "video/x-matroska"}
AUDIO_MIMES = {"audio/mpeg", "audio/ogg", "audio/mp4", "audio/aac", "audio/wav", "audio/x-wav"}
DOC_MIMES = {
    "application/pdf", "application/zip", "application/x-zip-compressed",
    "application/msword", "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.ms-excel", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-powerpoint", "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "text/plain", "text/csv", "application/json", "application/octet-stream",
}

# kind -> mime families the kind accepts
KIND_ALLOWED: dict[str, set[str]] = {
    "photo": IMAGE_MIMES,
    "avatar": IMAGE_MIMES,
    "video": VIDEO_MIMES,
    "voice": AUDIO_MIMES,
    "sticker": IMAGE_MIMES | {"application/x-tgsticker", "video/webm"},
    "document": DOC_MIMES | IMAGE_MIMES | VIDEO_MIMES | AUDIO_MIMES,
    "other": DOC_MIMES | IMAGE_MIMES | VIDEO_MIMES | AUDIO_MIMES,
}

# The bytes a kind must actually contain (a document may be anything allowed above).
KIND_MUST_BE_MEDIA = {"photo": IMAGE_MIMES, "avatar": IMAGE_MIMES, "video": VIDEO_MIMES,
                      "voice": AUDIO_MIMES, "sticker": IMAGE_MIMES | {"video/webm", "application/x-tgsticker"}}


@dataclass(frozen=True)
class Sniffed:
    mime: str
    extension: str | None
    is_media: bool


def sniff(head: bytes) -> Sniffed:
    """Best-effort content type from the magic bytes only."""
    if head.startswith(b"\xff\xd8"):
        return Sniffed("image/jpeg", "jpg", True)
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return Sniffed("image/png", "png", True)
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return Sniffed("image/gif", "gif", True)
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return Sniffed("image/webp", "webp", True)
    if head[4:8] == b"ftyp":
        brand = head[8:12]
        if brand.startswith(b"heic") or brand.startswith(b"heix") or brand.startswith(b"mif1"):
            return Sniffed("image/heic", "heic", True)
        if brand in (b"qt  ",):
            return Sniffed("video/quicktime", "mov", True)
        return Sniffed("video/mp4", "mp4", True)
    if head.startswith(b"\x1aE\xdf\xa3"):
        return Sniffed("video/x-matroska", "mkv", True)
    if head.startswith(b"OggS"):
        return Sniffed("audio/ogg", "ogg", True)
    if head.startswith(b"ID3") or head[:2] in (b"\xff\xfb", b"\xff\xf3"):
        return Sniffed("audio/mpeg", "mp3", True)
    if head.startswith(b"%PDF"):
        return Sniffed("application/pdf", "pdf", False)
    if head.startswith(b"PK\x03\x04"):
        return Sniffed("application/zip", "zip", False)
    return Sniffed("application/octet-stream", None, False)


def validate(kind: str, head: bytes, declared_mime: str | None) -> Sniffed:
    """Raises ValueError when the bytes do not match the claimed kind."""
    if kind not in KIND_ALLOWED:
        raise ValueError(f"unknown kind {kind!r}")
    detected = sniff(head)
    allowed = KIND_ALLOWED[kind]
    if detected.mime not in allowed:
        raise ValueError(
            f"content type {detected.mime} is not allowed for kind {kind!r}"
        )
    must = KIND_MUST_BE_MEDIA.get(kind)
    if must is not None and detected.mime not in must:
        raise ValueError(f"kind {kind!r} requires media content, got {detected.mime}")
    if declared_mime and must is not None and declared_mime.split(";")[0].strip() != detected.mime:
        # The browser's guess is wrong often enough that this is informative, but
        # the sniffed type always wins; a hard mismatch on media is rejected.
        if declared_mime.startswith(("image/", "video/", "audio/")) and detected.mime != declared_mime:
            raise ValueError(f"declared {declared_mime} but the content is {detected.mime}")
    return detected


def image_dimensions(path) -> tuple[int | None, int | None]:
    """Width/height for images, so the client can lay the bubble out before download."""
    try:
        from PIL import Image  # optional dependency

        with Image.open(path) as image:
            return image.width, image.height
    except Exception:
        return None, None


def safe_filename(name: str, fallback: str = "file") -> str:
    """No path separators, no control characters, no header injection."""
    cleaned = unicodedata.normalize("NFKC", name or "").replace("\\", "/").split("/")[-1]
    cleaned = "".join(ch for ch in cleaned if ch.isprintable() and ch not in '"\r\n\t')
    cleaned = cleaned.strip().strip(".")
    if not cleaned:
        cleaned = fallback
    return cleaned[:200]
