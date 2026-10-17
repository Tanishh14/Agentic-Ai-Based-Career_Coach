"""GitHub repositories, analysis scopes, scans, webhook receiver and webhook setup."""
from __future__ import annotations

import json
import secrets
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from ..config import get_settings
from ..database import session_scope, utcnow
from ..errors import AppError, NotConfigured
from ..integrations.github import GitHubClient, commit_url
from ..models import AnalysisScope, CommitRecord, EvidenceRecord, Repository, ScopeSnapshot, SnapshotFile, WebhookDelivery
from ..security import sha256_hex, verify_github_signature
from ..services import job_queue
from ..services.assessments import evidence_dict
from ..services.events import audit, emit
from ..services.integrations_store import get_connection, github_token, upsert_connection
from .deps import user_id

router = APIRouter(prefix="/api/github", tags=["github"])

DEFAULT_EXCLUDES = ["node_modules/", "vendor/", "dist/", "build/", ".venv/", "venv/", "__pycache__/", "*.min.js"]


def _client(uid: str) -> GitHubClient:
    with session_scope() as db:
        return GitHubClient(github_token(db, uid))


@router.get("/repos/available")
async def available_repos(owner: str | None = None, uid: str = Depends(user_id)) -> dict[str, Any]:
    gh = _client(uid)
    try:
        if gh.token and not owner:
            repos = await gh.list_repos()
            source = "authenticated user (token)"
        elif owner:
            repos = await gh.list_public_user_repos(owner)
            source = f"public repositories of {owner}"
        else:
            raise NotConfigured("GitHub repository listing", ["GitHub token"], action="Connect a GitHub token, or enter a username to list public repositories.")
    finally:
        await gh.close()
    return {"source": source, "repos": [{"github_id": str(r["id"]), "full_name": r["full_name"], "private": r["private"], "default_branch": r.get("default_branch"),
                                         "language": r.get("language"), "pushed_at": r.get("pushed_at"), "description": r.get("description"),
                                         "html_url": r["html_url"], "archived": r.get("archived"), "fork": r.get("fork"), "size": r.get("size")} for r in repos]}


@router.get("/repos/{owner}/{name}/branches")
async def branches(owner: str, name: str, uid: str = Depends(user_id)) -> list[dict[str, Any]]:
    gh = _client(uid)
    try:
        br = await gh.list_branches(f"{owner}/{name}")
    finally:
        await gh.close()
    return [{"name": b["name"], "sha": b["commit"]["sha"], "protected": b.get("protected")} for b in br]


class ScopeCreate(BaseModel):
    full_name: str = Field(pattern=r"^[\w.-]+/[\w.-]+$")
    branch: str | None = Field(default=None, max_length=200)
    include_patterns: list[str] = Field(default_factory=list, max_length=30)
    exclude_patterns: list[str] | None = Field(default=None, max_length=50)


@router.post("/scopes")
async def create_scope(body: ScopeCreate, uid: str = Depends(user_id)) -> dict[str, Any]:
    gh = _client(uid)
    try:
        r = await gh.get_repo(body.full_name)
        branch = body.branch or r.get("default_branch") or "main"
        await gh.branch_head(body.full_name, branch)  # validates branch + access
        langs = await gh.languages(body.full_name)
    finally:
        await gh.close()
    with session_scope() as db:
        repo = db.execute(select(Repository).where(Repository.user_id == uid, Repository.github_id == str(r["id"]))).scalar_one_or_none()
        if repo is None:
            repo = Repository(user_id=uid, github_id=str(r["id"]), owner=r["owner"]["login"], name=r["name"], full_name=r["full_name"],
                              default_branch=r.get("default_branch") or "main", private=bool(r.get("private")), html_url=r["html_url"],
                              description=r.get("description"), languages=langs)
            db.add(repo)
            db.flush()
        else:
            repo.full_name, repo.private, repo.languages, repo.connection_status = r["full_name"], bool(r.get("private")), langs, "connected"
        prev = db.execute(select(AnalysisScope).where(AnalysisScope.repository_id == repo.id).order_by(AnalysisScope.version.desc()).limit(1)).scalar_one_or_none()
        for s in db.execute(select(AnalysisScope).where(AnalysisScope.repository_id == repo.id, AnalysisScope.active.is_(True))).scalars():
            s.active = False
        sc = AnalysisScope(user_id=uid, repository_id=repo.id, version=(prev.version + 1) if prev else 1, branch=branch,
                           include_patterns=[p.strip() for p in body.include_patterns if p.strip()],
                           exclude_patterns=[p.strip() for p in (body.exclude_patterns if body.exclude_patterns is not None else DEFAULT_EXCLUDES) if p.strip()],
                           active=True, scan_status="queued")
        db.add(sc)
        db.flush()
        scope_id = sc.id
        audit("scope_created", {"repo": repo.full_name, "branch": branch, "scope_version": sc.version}, uid, db=db)
    job_queue.enqueue("scan_scope", {"scope_id": scope_id, "trigger": "scope_change" if prev else "initial"}, f"scan:{scope_id}:initial", user_id=uid)
    emit("scope_created", {"scope_id": scope_id, "repo": body.full_name, "branch": branch}, uid)
    return scope_detail(scope_id, uid)


@router.get("/scopes")
def list_scopes(include_inactive: bool = False, uid: str = Depends(user_id)) -> list[dict[str, Any]]:
    with session_scope() as db:
        q = select(AnalysisScope).where(AnalysisScope.user_id == uid)
        if not include_inactive:
            q = q.where(AnalysisScope.active.is_(True))
        ids = [s.id for s in db.execute(q.order_by(AnalysisScope.created_at.desc())).scalars()]
    return [scope_detail(i, uid, files=False) for i in ids]


@router.get("/scopes/{scope_id}")
def scope_detail(scope_id: str, uid: str = Depends(user_id), files: bool = True) -> dict[str, Any]:
    s = get_settings()
    with session_scope() as db:
        sc = db.get(AnalysisScope, scope_id)
        if sc is None or sc.user_id != uid:
            raise AppError("not_found", "Scope not found.", status=404)
        repo = db.get(Repository, sc.repository_id)
        snap = db.get(ScopeSnapshot, sc.last_snapshot_id) if sc.last_snapshot_id else None
        snaps = db.execute(select(ScopeSnapshot).where(ScopeSnapshot.scope_id == scope_id).order_by(ScopeSnapshot.created_at.desc()).limit(30)).scalars().all()
        ev_counts = dict(db.execute(select(EvidenceRecord.verification_status, func.count()).where(EvidenceRecord.scope_id == scope_id,
                                                                                                    EvidenceRecord.status == "current")
                                    .group_by(EvidenceRecord.verification_status)).all())
        d = {"id": sc.id, "version": sc.version, "active": sc.active, "branch": sc.branch, "include_patterns": sc.include_patterns,
             "exclude_patterns": sc.exclude_patterns, "scan_status": sc.scan_status, "scan_error": sc.scan_error,
             "last_analyzed_sha": sc.last_analyzed_sha, "last_polled_at": sc.last_polled_at, "last_seen_head_sha": sc.last_seen_head_sha,
             "last_poll_error": sc.last_poll_error, "last_event_at": sc.last_event_at, "last_event_kind": sc.last_event_kind,
             "poll_interval_seconds": s.poll_interval_seconds, "created_at": sc.created_at, "evidence_counts": ev_counts,
             "repository": {"id": repo.id, "github_id": repo.github_id, "full_name": repo.full_name, "private": repo.private, "html_url": repo.html_url,
                            "default_branch": repo.default_branch, "languages": repo.languages, "webhook_id": repo.webhook_id, "webhook_status": repo.webhook_status,
                            "description": repo.description},
             "snapshot": None,
             "history": [{"id": x.id, "commit_sha": x.commit_sha, "commit_url": commit_url(repo.full_name, x.commit_sha), "message": (x.commit_message or "").split("\n")[0][:140],
                          "mode": x.mode, "trigger": x.trigger, "changed": len(x.changed_paths or []), "created_at": x.created_at} for x in snaps]}
        if snap:
            d["snapshot"] = {"id": snap.id, "commit_sha": snap.commit_sha, "commit_url": commit_url(repo.full_name, snap.commit_sha),
                             "commit_message": snap.commit_message, "commit_date": snap.commit_date, "tree_file_count": snap.tree_file_count,
                             "scanned_file_count": snap.scanned_file_count, "languages": snap.languages, "detectors": snap.detectors,
                             "ignored_count": snap.ignored_count, "ignored": snap.ignored[:200] if files else None, "mode": snap.mode,
                             "trigger": snap.trigger, "changed_paths": snap.changed_paths if files else None, "created_at": snap.created_at}
            if files:
                d["snapshot"]["files"] = [{"path": f.path, "language": f.language, "kind": f.file_kind, "blob_sha": f.blob_sha}
                                          for f in db.execute(select(SnapshotFile).where(SnapshotFile.snapshot_id == snap.id).order_by(SnapshotFile.path)).scalars()]
        return d


@router.post("/scopes/{scope_id}/rescan")
def rescan(scope_id: str, full: bool = False, uid: str = Depends(user_id)) -> dict[str, Any]:
    with session_scope() as db:
        sc = db.get(AnalysisScope, scope_id)
        if sc is None or sc.user_id != uid:
            raise AppError("not_found", "Scope not found.", status=404)
    jid, created = job_queue.enqueue("scan_scope", {"scope_id": scope_id, "trigger": "manual_rescan", "force_full": full},
                                     f"scan:{scope_id}:manual:{utcnow()}", user_id=uid)
    return {"job_id": jid, "queued": created}


@router.delete("/scopes/{scope_id}")
def deactivate_scope(scope_id: str, uid: str = Depends(user_id)) -> dict[str, Any]:
    with session_scope() as db:
        sc = db.get(AnalysisScope, scope_id)
        if sc is None or sc.user_id != uid:
            raise AppError("not_found", "Scope not found.", status=404)
        sc.active = False
        audit("scope_removed", {"scope_id": scope_id}, uid, db=db)
    jid, _ = job_queue.enqueue("reassess_jobs", {"user_id": uid, "trigger": "scope_change"}, f"reassess:{uid}:{utcnow()}", user_id=uid)
    return {"deactivated": True, "reassessment_job": jid}


@router.get("/evidence")
def list_evidence(scope_id: str | None = None, skill_key: str | None = None, status: str = "current", verification: str | None = None,
                  uid: str = Depends(user_id)) -> list[dict[str, Any]]:
    with session_scope() as db:
        q = select(EvidenceRecord).where(EvidenceRecord.user_id == uid)
        if scope_id:
            q = q.where(EvidenceRecord.scope_id == scope_id)
        if skill_key:
            q = q.where(EvidenceRecord.skill_key == skill_key)
        if status != "all":
            q = q.where(EvidenceRecord.status == status)
        if verification:
            q = q.where(EvidenceRecord.verification_status == verification)
        return [evidence_dict(e, with_snippet=False) for e in db.execute(q.order_by(EvidenceRecord.created_at.desc()).limit(1000)).scalars()]


@router.get("/evidence/{evidence_id}")
def get_evidence(evidence_id: str, uid: str = Depends(user_id)) -> dict[str, Any]:
    with session_scope() as db:
        e = db.get(EvidenceRecord, evidence_id)
        if e is None or e.user_id != uid:
            raise AppError("not_found", "Evidence not found.", status=404)
        return evidence_dict(e)


@router.post("/evidence/{evidence_id}/investigate")
async def investigate(evidence_id: str, uid: str = Depends(user_id)) -> dict[str, Any]:
    from ..agents.investigator import investigate as inv

    return await inv(uid, evidence_id)


@router.get("/commits")
def commits(repository_id: str, uid: str = Depends(user_id)) -> list[dict[str, Any]]:
    with session_scope() as db:
        repo = db.get(Repository, repository_id)
        if repo is None or repo.user_id != uid:
            raise AppError("not_found", "Repository not found.", status=404)
        rows = db.execute(select(CommitRecord).where(CommitRecord.repository_id == repository_id).order_by(CommitRecord.created_at.desc()).limit(100)).scalars()
        return [{"sha": c.sha, "message": c.message, "author_date": c.author_date, "html_url": c.html_url, "changed_paths": c.changed_paths} for c in rows]


# ------------------------------------------------------------------ webhooks


def _webhook_secret(uid: str | None = None) -> str:
    s = get_settings()
    if s.github_webhook_secret:
        return s.github_webhook_secret
    if uid:
        with session_scope() as db:
            c = get_connection(db, uid, "github_webhook")
            from ..services.integrations_store import connection_secret

            return connection_secret(c) or ""
    return ""


def _all_webhook_secrets() -> list[str]:
    s = get_settings()
    out = [s.github_webhook_secret] if s.github_webhook_secret else []
    from ..models import IntegrationConnection
    from ..services.integrations_store import connection_secret

    with session_scope() as db:
        for c in db.execute(select(IntegrationConnection).where(IntegrationConnection.provider == "github_webhook")).scalars():
            sec = connection_secret(c)
            if sec:
                out.append(sec)
    return out


@router.post("/webhook", include_in_schema=True)
async def webhook(request: Request) -> JSONResponse:
    raw = await request.body()
    sig = request.headers.get("x-hub-signature-256")
    delivery = request.headers.get("x-github-delivery") or ""
    event = request.headers.get("x-github-event") or ""
    secrets_ = _all_webhook_secrets()
    if not secrets_:
        return JSONResponse({"error": {"code": "webhook_not_configured", "message": "No webhook secret configured."}}, status_code=503)
    if not any(verify_github_signature(sec, raw, sig) for sec in secrets_):
        with session_scope() as db:
            if delivery and not db.execute(select(WebhookDelivery.id).where(WebhookDelivery.delivery_id == delivery)).first():
                db.add(WebhookDelivery(delivery_id=delivery[:100], event=event, signature_status="invalid", payload_digest=sha256_hex(raw),
                                       status="rejected", error="invalid or missing X-Hub-Signature-256"))
        return JSONResponse({"error": {"code": "invalid_signature", "message": "Signature verification failed."}}, status_code=401)
    if not delivery:
        return JSONResponse({"error": {"code": "missing_delivery_id", "message": "X-GitHub-Delivery header missing."}}, status_code=400)
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return JSONResponse({"error": {"code": "bad_json", "message": "Body is not JSON."}}, status_code=400)
    repo_info = payload.get("repository") or {}
    ref = payload.get("ref")
    after = payload.get("after")
    from ..database import immediate_transaction

    with immediate_transaction() as db:
        if db.execute(select(WebhookDelivery.id).where(WebhookDelivery.delivery_id == delivery)).first():
            return JSONResponse({"status": "duplicate", "delivery_id": delivery}, status_code=200)
        d = WebhookDelivery(delivery_id=delivery[:100], event=event, signature_status="valid", repository_full_name=repo_info.get("full_name"),
                            ref=ref, after_sha=after, payload_digest=sha256_hex(raw), status="received")
        db.add(d)
    if event == "ping":
        _mark(delivery, "processed", None)
        return JSONResponse({"status": "pong"})
    if event != "push":
        _mark(delivery, "ignored", f"event {event} not handled")
        return JSONResponse({"status": "ignored", "event": event})
    branch = (ref or "").removeprefix("refs/heads/")
    gid = str(repo_info.get("id", ""))
    with session_scope() as db:
        matches = db.execute(select(AnalysisScope, Repository).join(Repository, AnalysisScope.repository_id == Repository.id)
                             .where(Repository.github_id == gid, AnalysisScope.active.is_(True), AnalysisScope.branch == branch)).all()
        targets = [(sc.id, sc.user_id, repo.full_name) for sc, repo in matches]
    if not targets:
        _mark(delivery, "ignored", f"no active scope for {repo_info.get('full_name')}@{branch}")
        return JSONResponse({"status": "ignored", "reason": "repository/branch not in an active analysis scope"})
    job_ids = []
    for scope_id, uid, full in targets:
        jid, created = job_queue.enqueue("scan_scope", {"scope_id": scope_id, "trigger": "webhook", "trigger_ref": delivery},
                                         f"scan:{scope_id}:{after}", user_id=uid)
        job_ids.append(jid)
        with session_scope() as db:
            sc = db.get(AnalysisScope, scope_id)
            sc.last_event_at, sc.last_event_kind = utcnow(), "webhook"
        emit("change_detected", {"scope_id": scope_id, "repo": full, "branch": branch, "head": after, "via": "webhook", "delivery_id": delivery,
                                 "commits": len(payload.get("commits") or [])}, uid)
    _mark(delivery, "queued", None, job_ids[0] if job_ids else None)
    return JSONResponse({"status": "queued", "jobs": job_ids}, status_code=202)


def _mark(delivery: str, status: str, error: str | None, job_id: str | None = None) -> None:
    with session_scope() as db:
        d = db.execute(select(WebhookDelivery).where(WebhookDelivery.delivery_id == delivery)).scalar_one_or_none()
        if d:
            d.status, d.error, d.processed_at, d.job_id = status, error, utcnow(), job_id


@router.get("/webhook/deliveries")
def deliveries(uid: str = Depends(user_id)) -> list[dict[str, Any]]:
    with session_scope() as db:
        names = {r.full_name for r in db.execute(select(Repository).where(Repository.user_id == uid)).scalars()}
        rows = db.execute(select(WebhookDelivery).order_by(WebhookDelivery.received_at.desc()).limit(100)).scalars()
        return [{"delivery_id": d.delivery_id, "event": d.event, "signature_status": d.signature_status, "repository": d.repository_full_name,
                 "ref": d.ref, "after_sha": d.after_sha, "received_at": d.received_at, "processed_at": d.processed_at, "status": d.status,
                 "error": d.error, "job_id": d.job_id} for d in rows if d.repository_full_name in names or d.repository_full_name is None]


class HookSetup(BaseModel):
    repository_id: str
    confirm: bool = False


@router.post("/webhook/setup")
async def setup_webhook(body: HookSetup, uid: str = Depends(user_id)) -> dict[str, Any]:
    s = get_settings()
    if not body.confirm:
        raise AppError("confirmation_required", "Confirm creating a push webhook on this repository.", status=428)
    if not s.app_public_url.startswith("https://"):
        raise NotConfigured("Inbound GitHub webhooks", ["APP_PUBLIC_URL (public https URL, e.g. an ngrok tunnel)"],
                            action="Set APP_PUBLIC_URL in .env and restart. Without it, SHA polling detects pushes automatically.")
    with session_scope() as db:
        repo = db.get(Repository, body.repository_id)
        if repo is None or repo.user_id != uid:
            raise AppError("not_found", "Repository not found.", status=404)
        secret = _webhook_secret(uid)
        if not secret:
            secret = secrets.token_hex(24)
            upsert_connection(db, uid, "github_webhook", secret=secret, config={"generated": True})
        full = repo.full_name
        token = github_token(db, uid)
    url = f"{s.app_public_url.rstrip('/')}/api/github/webhook"
    gh = GitHubClient(token)
    try:
        existing = [h for h in await gh.list_hooks(full) if (h.get("config") or {}).get("url") == url]
        hook = existing[0] if existing else await gh.create_hook(full, url, secret)
    finally:
        await gh.close()
    with session_scope() as db:
        repo = db.get(Repository, body.repository_id)
        repo.webhook_id, repo.webhook_status = str(hook["id"]), "active"
        audit("external_write", {"action": "webhook_created", "repo": full, "url": url}, uid, db=db)
    return {"hook_id": hook["id"], "url": url, "events": hook.get("events")}
