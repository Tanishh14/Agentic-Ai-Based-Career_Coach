"""Local user + integration credentials (encrypted at rest).

This build is a single-user local application: one `users` row is created on first
start and every record is owned by it. Object-level checks still filter by user_id
so the schema is ready for multi-user auth.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import session_scope, utcnow
from ..models import IntegrationConnection, User
from ..security import decrypt_secret, encrypt_secret, mask_secret

DEFAULT_USER_SETTINGS: dict[str, Any] = {
    "allow_code_snippets_to_llm": False,
    "auto_close_issues": False,
    "keep_interview_audio": False,
    "audio_upload_consent": False,
    "sandbox_allow_network": False,
    "show_on_leaderboard": True,
}


def get_or_create_user(db: Session) -> User:
    user = db.execute(select(User).order_by(User.created_at).limit(1)).scalar_one_or_none()
    if user is None:
        user = User(display_name="", settings=dict(DEFAULT_USER_SETTINGS))
        db.add(user)
        db.flush()
    return user


def current_user_id() -> str:
    with session_scope() as db:
        return get_or_create_user(db).id


def user_settings(db: Session, user_id: str) -> dict[str, Any]:
    u = db.get(User, user_id)
    s = dict(DEFAULT_USER_SETTINGS)
    if u and u.settings:
        s.update(u.settings)
    return s


def get_connection(db: Session, user_id: str, provider: str) -> IntegrationConnection | None:
    return db.execute(
        select(IntegrationConnection).where(IntegrationConnection.user_id == user_id, IntegrationConnection.provider == provider)
    ).scalar_one_or_none()


def upsert_connection(
    db: Session,
    user_id: str,
    provider: str,
    *,
    secret: str | None = None,
    config: dict[str, Any] | None = None,
    scopes: list[str] | None = None,
    status: str = "connected",
    clear_secret: bool = False,
) -> IntegrationConnection:
    conn = get_connection(db, user_id, provider)
    if conn is None:
        conn = IntegrationConnection(user_id=user_id, provider=provider, config={}, scopes=[])
        db.add(conn)
    if secret:
        conn.encrypted_credential = encrypt_secret(secret)
    elif clear_secret:
        conn.encrypted_credential = None
    if config is not None:
        merged = dict(conn.config or {})
        merged.update(config)
        conn.config = merged
    if scopes is not None:
        conn.scopes = scopes
    conn.status = status
    conn.last_sync_at = utcnow()
    conn.last_error = None
    db.flush()
    return conn


def connection_secret(conn: IntegrationConnection | None) -> str | None:
    return decrypt_secret(conn.encrypted_credential) if conn else None


@dataclass
class LLMProviderConfig:
    name: str
    base_url: str
    api_key: str
    model: str
    source: str  # settings | env

    @property
    def is_local(self) -> bool:
        return any(h in self.base_url for h in ("localhost", "127.0.0.1", "[::1]"))


def llm_providers(db: Session, user_id: str) -> list[LLMProviderConfig]:
    s = get_settings()
    out: list[LLMProviderConfig] = []
    for slot, env_base, env_key, env_model in (
        ("llm_primary", s.llm_base_url, s.llm_api_key, s.llm_model),
        ("llm_fallback", s.llm_fallback_base_url, s.llm_fallback_api_key, s.llm_fallback_model),
    ):
        conn = get_connection(db, user_id, slot)
        if conn and conn.status == "connected" and (conn.config or {}).get("base_url") and (conn.config or {}).get("model"):
            out.append(LLMProviderConfig(slot, conn.config["base_url"].rstrip("/"), connection_secret(conn) or "", conn.config["model"], "settings"))
        elif env_base and env_model:
            out.append(LLMProviderConfig(slot, env_base.rstrip("/"), env_key, env_model, "env"))
    return out


def github_token(db: Session, user_id: str) -> str | None:
    conn = get_connection(db, user_id, "github")
    tok = connection_secret(conn) if conn and conn.status == "connected" else None
    return tok or (get_settings().github_token or None)


def provider_secret(db: Session, user_id: str, provider: str, env_value: str = "") -> str | None:
    conn = get_connection(db, user_id, provider)
    return (connection_secret(conn) if conn and conn.status == "connected" else None) or (env_value or None)


def provider_config(db: Session, user_id: str, provider: str) -> dict[str, Any]:
    conn = get_connection(db, user_id, provider)
    return dict(conn.config or {}) if conn else {}


def describe_connection(conn: IntegrationConnection | None) -> dict[str, Any]:
    if conn is None:
        return {"status": "disconnected"}
    secret = connection_secret(conn)
    return {
        "status": conn.status,
        "config": conn.config or {},
        "scopes": conn.scopes or [],
        "credential_hint": mask_secret(secret),
        "last_sync_at": conn.last_sync_at,
        "last_error": conn.last_error,
    }
