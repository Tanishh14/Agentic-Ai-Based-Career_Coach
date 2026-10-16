"""Private local media storage. SQLite keeps only metadata; files live outside the DB."""
from __future__ import annotations

import secrets
from pathlib import Path

from ..config import get_settings
from ..database import session_scope, utcnow
from ..errors import AppError
from ..models import MediaFile
from ..security import sha256_hex

AUDIO_TYPES = {"audio/webm", "audio/ogg", "audio/mp4", "audio/mpeg", "audio/wav", "audio/x-wav", "video/webm", "audio/aac"}


def _root() -> Path:
    p = Path(get_settings().media_storage_dir).resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


def path_for(storage_key: str) -> Path:
    if not storage_key or "/" in storage_key or "\\" in storage_key or ".." in storage_key:
        raise AppError("bad_storage_key", "Invalid storage key.", status=400)
    p = (_root() / storage_key).resolve()
    if _root() not in p.parents:
        raise AppError("bad_storage_key", "Invalid storage key.", status=400)
    return p


def save_media(user_id: str, data: bytes, mime: str, purpose: str) -> MediaFile:
    base_mime = (mime or "").split(";")[0].strip().lower()
    if base_mime not in AUDIO_TYPES:
        raise AppError("unsupported_media", f"Unsupported media type {mime}.", status=415)
    if len(data) > get_settings().max_upload_bytes:
        raise AppError("media_too_large", "Recording exceeds the upload limit.", status=413)
    if len(data) < 200:
        raise AppError("media_empty", "The recording is empty.", status=422)
    ext = {"audio/webm": "webm", "video/webm": "webm", "audio/ogg": "ogg", "audio/mp4": "m4a", "audio/mpeg": "mp3", "audio/wav": "wav",
           "audio/x-wav": "wav", "audio/aac": "aac"}[base_mime]
    key = f"{secrets.token_hex(16)}.{ext}"
    path_for(key).write_bytes(data)
    with session_scope() as db:
        m = MediaFile(user_id=user_id, storage_key=key, mime=base_mime, size=len(data), sha256=sha256_hex(data), purpose=purpose)
        db.add(m)
        db.flush()
        db.expunge(m)
        return m


def delete_media(media_id: str, user_id: str) -> bool:
    with session_scope() as db:
        m = db.get(MediaFile, media_id)
        if m is None or m.user_id != user_id:
            return False
        if m.retention_status != "deleted":
            try:
                path_for(m.storage_key).unlink(missing_ok=True)
            except AppError:
                pass
            m.retention_status = "deleted"
            m.deleted_at = utcnow()
        return True
