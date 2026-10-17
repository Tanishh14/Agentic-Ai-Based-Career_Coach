"""Health, provider status, settings, integrations, privacy (export/delete), taxonomy."""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import delete, select

from ..analyzers.detectors import DETECTOR_FAMILIES
from ..analyzers.taxonomy import taxonomy_document
from ..config import get_settings
from ..database import db_health, session_scope
from ..errors import AppError
from ..integrations.github import GitHubClient, rate_state
from ..integrations.job_sources import PROVIDER_INFO
from ..integrations.llm import providers_for, test_provider
from ..integrations.transcription import transcription_status
from ..models import (AuditEvent, EvidenceFreshnessEvent, GapTask, IntegrationConnection, InterviewSession, JobPosting, JobSearchRun, LeetCodeSnapshot,
                      MediaFile, ProfileDocument, Repository, SandboxRun, SkillPassport, SystemState, User)
from ..services import job_queue
from ..services.events import audit
from ..services.integrations_store import (DEFAULT_USER_SETTINGS, LLMProviderConfig, describe_connection, get_connection, github_token,
                                           provider_secret, upsert_connection, user_settings)
from ..services.sandbox import docker_status
from ..services.storage import delete_media
from .deps import user_id

router = APIRouter(prefix="/api", tags=["system"])


@router.get("/health")
def health() -> dict[str, Any]:
    return {"ok": True, "db": db_health()}


@router.get("/status")
def status(uid: str = Depends(user_id)) -> dict[str, Any]:
    s = get_settings()
    with session_scope() as db:
        gh = get_connection(db, uid, "github")
        hb = db.get(SystemState, "worker_heartbeat")
        user = db.get(User, uid)
        has_token = bool(github_token(db, uid))
        adzuna_ok = bool((get_connection(db, uid, "adzuna") and provider_secret(db, uid, "adzuna")) or (s.adzuna_app_id and s.adzuna_app_key))
        serp_ok = bool(provider_secret(db, uid, "serpapi", s.serpapi_api_key))
        counts = {
            "jobs": db.query(JobPosting).filter(JobPosting.user_id == uid).count(),
            "repositories": db.query(Repository).filter(Repository.user_id == uid).count(),
            "profiles": db.query(ProfileDocument).filter(ProfileDocument.user_id == uid, ProfileDocument.active.is_(True)).count(),
        }
    from datetime import datetime, timezone

    worker = {"running": False}
    if hb:
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(hb.value["at"])).total_seconds()
        worker = {"running": age < 60, "last_heartbeat": hb.value["at"], "age_seconds": int(age), "poll_interval_seconds": hb.value.get("poll_interval_seconds")}
    llm = providers_for(uid)
    tr = transcription_status(uid)
    return {
        "user": {"id": uid, "display_name": user.display_name if user else ""},
        "counts": counts,
        "worker": worker,
        "queue": job_queue.queue_stats(),
        "github": {**describe_connection(gh), "token_present": has_token, "rate_limit": rate_state(),
                   "webhook": {"public_url": s.app_public_url or None, "secret_configured": bool(s.github_webhook_secret),
                               "endpoint": f"{(s.app_public_url or s.app_base_url).rstrip('/')}/api/github/webhook"}},
        "llm": {"configured": bool(llm), "providers": [{"slot": p.name, "base_url": p.base_url, "model": p.model, "source": p.source, "local": p.is_local} for p in llm],
                "missing": [] if llm else ["LLM base URL + model (Settings → AI provider)"]},
        "transcription": tr,
        "job_providers": {k: {**v, "configured": v["keyless"] or (k == "adzuna" and adzuna_ok) or (k == "serpapi" and serp_ok)} for k, v in PROVIDER_INFO.items()},
        "sandbox": docker_status(),
        "database": db_health(),
        "detectors": DETECTOR_FAMILIES,
    }


class SettingsPatch(BaseModel):
    display_name: str | None = Field(default=None, max_length=200)
    allow_code_snippets_to_llm: bool | None = None
    auto_close_issues: bool | None = None
    keep_interview_audio: bool | None = None
    sandbox_allow_network: bool | None = None
    show_on_leaderboard: bool | None = None


@router.get("/settings")
def get_user_settings(uid: str = Depends(user_id)) -> dict[str, Any]:
    with session_scope() as db:
        u = db.get(User, uid)
        return {"display_name": u.display_name, **user_settings(db, uid)}


@router.patch("/settings")
def patch_settings(body: SettingsPatch, uid: str = Depends(user_id)) -> dict[str, Any]:
    with session_scope() as db:
        u = db.get(User, uid)
        st = dict(DEFAULT_USER_SETTINGS)
        st.update(u.settings or {})
        for k, v in body.model_dump(exclude_none=True).items():
            if k == "display_name":
                u.display_name = v
            else:
                st[k] = v
        u.settings = st
        audit("settings_changed", body.model_dump(exclude_none=True), uid, db=db)
    return get_user_settings(uid)


class GitHubConnect(BaseModel):
    token: str = Field(min_length=20, max_length=300)


@router.post("/integrations/github")
async def connect_github(body: GitHubConnect, uid: str = Depends(user_id)) -> dict[str, Any]:
    async with GitHubClient(body.token.strip()) as gh:
        me = await gh.viewer()
    with session_scope() as db:
        upsert_connection(db, uid, "github", secret=body.token.strip(), config={"login": me["login"], "avatar_url": me["avatar_url"],
                                                                                "html_url": me["html_url"], "token_type": me["token_type"]},
                          scopes=me["scopes"])
        u = db.get(User, uid)
        if not u.display_name:
            u.display_name = me.get("name") or me["login"]
        audit("integration_connected", {"provider": "github", "login": me["login"], "scopes": me["scopes"]}, uid, db=db)
    return {"connected": True, "login": me["login"], "scopes": me["scopes"], "token_type": me["token_type"]}


@router.delete("/integrations/{provider}")
def disconnect(provider: str, uid: str = Depends(user_id)) -> dict[str, Any]:
    with session_scope() as db:
        c = get_connection(db, uid, provider)
        if c:
            c.encrypted_credential = None
            c.status = "disconnected"
        audit("integration_disconnected", {"provider": provider}, uid, db=db)
    return {"disconnected": True}


class LLMConfig(BaseModel):
    slot: str = Field(pattern="^(llm_primary|llm_fallback)$")
    base_url: str = Field(min_length=8, max_length=300)
    model: str = Field(min_length=1, max_length=200)
    api_key: str | None = Field(default=None, max_length=400)


@router.post("/integrations/llm")
async def set_llm(body: LLMConfig, uid: str = Depends(user_id)) -> dict[str, Any]:
    base = body.base_url.rstrip("/")
    with session_scope() as db:
        existing = get_connection(db, uid, body.slot)
        from ..services.integrations_store import connection_secret

        key = body.api_key or (connection_secret(existing) if existing else None) or ""
    result = await test_provider(LLMProviderConfig(body.slot, base, key, body.model, "settings"))
    with session_scope() as db:
        upsert_connection(db, uid, body.slot, secret=body.api_key or None, config={"base_url": base, "model": body.model},
                          status="connected" if result["ok"] else "error")
        c = get_connection(db, uid, body.slot)
        c.last_error = None if result["ok"] else result.get("error")
        audit("integration_connected", {"provider": body.slot, "base_url": base, "model": body.model, "test_ok": result["ok"]}, uid, db=db)
    return {"test": result}


@router.post("/integrations/llm/test")
async def test_llm(uid: str = Depends(user_id)) -> dict[str, Any]:
    return {"results": [{"slot": p.name, "model": p.model, **(await test_provider(p))} for p in providers_for(uid)]}


@router.get("/integrations/ollama/models")
async def ollama_models() -> dict[str, Any]:
    import httpx

    try:
        async with httpx.AsyncClient(timeout=3) as c:
            r = await c.get("http://127.0.0.1:11434/api/tags")
        models = [{"name": m["name"], "size": m.get("size"), "capabilities": m.get("capabilities")} for m in r.json().get("models", [])]
        return {"available": True, "base_url": "http://127.0.0.1:11434/v1", "models": models}
    except Exception:  # noqa: BLE001
        return {"available": False, "models": []}


class KeyedProvider(BaseModel):
    provider: str = Field(pattern="^(adzuna|serpapi|transcription)$")
    secret: str | None = Field(default=None, max_length=400)
    config: dict[str, Any] = Field(default_factory=dict)


@router.post("/integrations/provider")
def set_provider(body: KeyedProvider, uid: str = Depends(user_id)) -> dict[str, Any]:
    allowed = {"adzuna": {"app_id", "country"}, "serpapi": set(), "transcription": {"provider", "model", "base_url"}}[body.provider]
    cfg = {k: v for k, v in body.config.items() if k in allowed}
    if body.provider == "transcription" and cfg.get("provider") not in (None, "local_whisper", "openai_compatible", "none"):
        raise AppError("bad_provider", "Unknown transcription provider.", status=422)
    with session_scope() as db:
        upsert_connection(db, uid, body.provider, secret=body.secret or None, config=cfg)
        audit("integration_connected", {"provider": body.provider, "config": cfg}, uid, db=db)
    return {"saved": True}


@router.get("/integrations")
def integrations(uid: str = Depends(user_id)) -> dict[str, Any]:
    with session_scope() as db:
        conns = db.execute(select(IntegrationConnection).where(IntegrationConnection.user_id == uid)).scalars().all()
        return {c.provider: describe_connection(c) for c in conns}


@router.get("/taxonomy")
def taxonomy() -> dict[str, Any]:
    return taxonomy_document()


@router.get("/audit")
def audit_log(limit: int = 100, uid: str = Depends(user_id)) -> list[dict[str, Any]]:
    with session_scope() as db:
        rows = db.execute(select(AuditEvent).where(AuditEvent.user_id == uid).order_by(AuditEvent.created_at.desc()).limit(min(limit, 500))).scalars()
        return [{"id": a.id, "kind": a.kind, "detail": a.detail, "created_at": a.created_at} for a in rows]


@router.get("/privacy/export")
def export_data(uid: str = Depends(user_id)) -> dict[str, Any]:
    from ..database import get_engine
    from ..models import Base

    out: dict[str, Any] = {}
    with get_engine().connect() as conn:
        for table in Base.metadata.sorted_tables:
            if "user_id" in table.c:
                rows = conn.execute(select(table).where(table.c.user_id == uid)).mappings().all()
                out[table.name] = [{k: v for k, v in r.items() if k not in ("encrypted_credential",)} for r in rows]
    audit("data_exported", {"tables": len(out)}, uid)
    return out


class DeleteAll(BaseModel):
    confirm: str


@router.post("/privacy/delete-all")
def delete_all(body: DeleteAll, uid: str = Depends(user_id)) -> dict[str, Any]:
    if body.confirm != "DELETE MY DATA":
        raise AppError("confirmation_required", "Type DELETE MY DATA to confirm.", status=428)
    with session_scope() as db:
        media = [m for (m,) in db.execute(select(MediaFile.id).where(MediaFile.user_id == uid))]
    for m in media:
        delete_media(m, uid)
    with session_scope() as db:
        for table in (SkillPassport, GapTask, SandboxRun, InterviewSession, EvidenceFreshnessEvent, JobPosting, JobSearchRun, Repository,
                      ProfileDocument, LeetCodeSnapshot, IntegrationConnection, MediaFile):
            db.execute(delete(table).where(table.user_id == uid))
    cache = Path(get_settings().blob_cache_dir)
    if cache.exists():
        shutil.rmtree(cache, ignore_errors=True)
        cache.mkdir(parents=True, exist_ok=True)
    audit("data_deleted", {"media_files": len(media)}, uid)
    return {"deleted": True}
