"""Student accounts: register/login with a name and password, stored in SQLite.

Passwords are hashed with scrypt (stdlib) and a per-user salt; sessions are random bearer
tokens of which only a SHA-256 is stored. No e-mail or other personal data is collected.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from ..database import session_scope, utcnow
from ..errors import AppError
from ..models import AnalysisScope, JobPosting, User, UserSession
from .events import audit
from .integrations_store import DEFAULT_USER_SETTINGS

SESSION_DAYS = 30
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{1,38}[A-Za-z0-9]$")


def _hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(dk).decode()


def _check_password(password: str, stored: str | None) -> bool:
    if not stored or not stored.startswith("scrypt$"):
        return False
    _, salt_b64, dk_b64 = stored.split("$")
    expected = _hash_password(password, base64.b64decode(salt_b64)).split("$")[2]
    return hmac.compare_digest(expected, dk_b64)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _validate(name: str, password: str) -> tuple[str, str]:
    name = " ".join((name or "").split())
    if not NAME_RE.match(name):
        raise AppError("invalid_name", "Name must be 3–40 characters: letters, numbers, spaces, dot, dash or underscore.", status=422)
    if len(password or "") < 6:
        raise AppError("weak_password", "Password must be at least 6 characters.", status=422)
    return name, name.lower()


def _new_session(db, user_id: str) -> str:
    token = secrets.token_urlsafe(32)
    db.add(UserSession(token_hash=_token_hash(token), user_id=user_id,
                       expires_at=(datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)).isoformat()))
    return token


def register(name: str, password: str) -> dict:
    display, username = _validate(name, password)
    with session_scope() as db:
        if db.execute(select(User.id).where(User.username == username)).first():
            raise AppError("name_taken", "That name is already registered. Log in instead, or pick another name.", status=409)
        # The first account adopts data created before accounts existed (single-user installs).
        legacy = db.execute(select(User).where(User.username.is_(None)).order_by(User.created_at)).scalars().first()
        adopt = False
        if legacy is not None:
            has_data = db.execute(select(func.count()).select_from(JobPosting).where(JobPosting.user_id == legacy.id)).scalar() or \
                db.execute(select(func.count()).select_from(AnalysisScope).where(AnalysisScope.user_id == legacy.id)).scalar()
            registered = db.execute(select(func.count()).select_from(User).where(User.username.is_not(None))).scalar()
            adopt = bool(has_data) and registered == 0
        if adopt:
            user = legacy
        else:
            user = User(settings=dict(DEFAULT_USER_SETTINGS))
            db.add(user)
        user.username, user.display_name, user.password_hash = username, display, _hash_password(password)
        user.last_login_at = utcnow()
        db.flush()
        token = _new_session(db, user.id)
        audit("account_registered", {"name": display, "adopted_existing_data": adopt}, user.id, db=db)
        return {"token": token, "user": {"id": user.id, "name": display}, "adopted_existing_data": adopt}


def login(name: str, password: str) -> dict:
    username = " ".join((name or "").split()).lower()
    with session_scope() as db:
        user = db.execute(select(User).where(User.username == username)).scalar_one_or_none()
        if user is None or not _check_password(password or "", user.password_hash):
            raise AppError("invalid_credentials", "Name or password is incorrect.", status=401)
        user.last_login_at = utcnow()
        token = _new_session(db, user.id)
        audit("login", {}, user.id, db=db)
        return {"token": token, "user": {"id": user.id, "name": user.display_name}}


def logout(token: str) -> None:
    with session_scope() as db:
        s = db.get(UserSession, _token_hash(token))
        if s:
            db.delete(s)


def user_for_token(token: str | None) -> str | None:
    if not token:
        return None
    now = datetime.now(timezone.utc)
    with session_scope() as db:
        s = db.get(UserSession, _token_hash(token))
        if s is None or datetime.fromisoformat(s.expires_at) < now:
            return None
        if not s.last_seen_at or (now - datetime.fromisoformat(s.last_seen_at)).total_seconds() > 300:
            s.last_seen_at = now.isoformat()
        return s.user_id
