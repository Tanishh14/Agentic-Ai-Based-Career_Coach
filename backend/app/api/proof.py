"""Phase 3: gap-to-proof tasks, sandbox runs, skill passports, market/roadmap/freshness insights."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select

from ..database import session_scope
from ..errors import AppError
from ..models import EvidenceFreshnessEvent, GapTask, Repository, SandboxRun, SkillPassport
from ..services import gap_tasks, insights, passport, sandbox
from .deps import user_id

router = APIRouter(prefix="/api", tags=["proof"])


def task_dict(t: GapTask, repo: Repository | None) -> dict[str, Any]:
    return {"id": t.id, "assessment_row_id": t.assessment_row_id, "job_posting_id": t.job_posting_id, "requirement_id": t.requirement_id, "skill_key": t.skill_key,
            "repository": {"id": repo.id, "full_name": repo.full_name} if repo else None, "title": t.title, "body": t.body, "objective": t.objective,
            "suggested_files": t.suggested_files, "acceptance_criteria": t.acceptance_criteria, "risks": t.risks, "effort_estimate": t.effort_estimate,
            "generator": t.generator, "status": t.status, "confirmed_at": t.confirmed_at, "github_issue_number": t.github_issue_number,
            "github_issue_url": t.github_issue_url, "last_verification": t.last_verification, "verified_commit_sha": t.verified_commit_sha,
            "created_at": t.created_at, "updated_at": t.updated_at,
            "required_permission": "Fine-grained token: Issues → Read and write on this repository (classic token: public_repo or repo)."}


class Propose(BaseModel):
    assessment_row_id: str
    repository_id: str | None = None


@router.post("/tasks/propose")
async def propose(body: Propose, uid: str = Depends(user_id)) -> dict[str, Any]:
    tid = await gap_tasks.propose(uid, body.assessment_row_id, body.repository_id)
    with session_scope() as db:
        t = db.get(GapTask, tid)
        res = gap_tasks.evaluate_criteria(db, t, None)
        t.last_verification = res
        return task_dict(t, db.get(Repository, t.repository_id))


@router.get("/tasks")
def list_tasks(job_posting_id: str | None = None, uid: str = Depends(user_id)) -> list[dict[str, Any]]:
    with session_scope() as db:
        q = select(GapTask).where(GapTask.user_id == uid)
        if job_posting_id:
            q = q.where(GapTask.job_posting_id == job_posting_id)
        return [task_dict(t, db.get(Repository, t.repository_id) if t.repository_id else None) for t in db.execute(q.order_by(GapTask.created_at.desc())).scalars()]


class Confirm(BaseModel):
    confirm: bool
    repository_full_name: str


@router.post("/tasks/{task_id}/issue")
async def create_issue(task_id: str, body: Confirm, uid: str = Depends(user_id)) -> dict[str, Any]:
    return await gap_tasks.create_issue(uid, task_id, body.confirm, body.repository_full_name)


@router.post("/tasks/{task_id}/verify")
def verify(task_id: str, uid: str = Depends(user_id)) -> dict[str, Any]:
    with session_scope() as db:
        t = db.get(GapTask, task_id)
        if t is None or t.user_id != uid:
            raise AppError("not_found", "Task not found.", status=404)
        from ..models import AnalysisScope

        sc = db.execute(select(AnalysisScope).where(AnalysisScope.repository_id == t.repository_id, AnalysisScope.active.is_(True))).scalar_one_or_none()
        res = gap_tasks.evaluate_criteria(db, t, sc.last_analyzed_sha if sc else None)
        t.last_verification = res
        if res["all_passed"]:
            t.status = "criteria_met"
            t.verified_commit_sha = res["commit_sha"]
        return res


class CloseBody(BaseModel):
    confirm: bool


@router.post("/tasks/{task_id}/close")
async def close(task_id: str, body: CloseBody, uid: str = Depends(user_id)) -> dict[str, Any]:
    return await gap_tasks.close_issue(uid, task_id, body.confirm)


@router.delete("/tasks/{task_id}")
def dismiss(task_id: str, uid: str = Depends(user_id)) -> dict[str, Any]:
    with session_scope() as db:
        t = db.get(GapTask, task_id)
        if t is None or t.user_id != uid:
            raise AppError("not_found", "Task not found.", status=404)
        t.status = "dismissed"
    return {"dismissed": True}


# ------------------------------------------------------------------ sandbox


@router.get("/sandbox/status")
def sandbox_status() -> dict[str, Any]:
    return sandbox.docker_status()


@router.get("/sandbox/propose/{repository_id}")
def sandbox_propose(repository_id: str, uid: str = Depends(user_id)) -> dict[str, Any]:
    return {**sandbox.propose_command(uid, repository_id), "status": sandbox.docker_status()}


class RunBody(BaseModel):
    repository_id: str
    command: str = Field(min_length=2, max_length=2000)
    image: str = Field(min_length=3, max_length=200, pattern=r"^[\w./:-]+$")
    commit_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    consent: bool


@router.post("/sandbox/runs")
def sandbox_run(body: RunBody, uid: str = Depends(user_id)) -> dict[str, Any]:
    rid = sandbox.request_run(uid, body.repository_id, body.command, body.image, body.commit_sha, body.consent)
    return sandbox_get(rid, uid)


@router.get("/sandbox/runs")
def sandbox_list(uid: str = Depends(user_id)) -> list[dict[str, Any]]:
    with session_scope() as db:
        return [sandbox_get(r.id, uid) for r in db.execute(select(SandboxRun).where(SandboxRun.user_id == uid).order_by(SandboxRun.created_at.desc()).limit(50)).scalars()]


@router.get("/sandbox/runs/{run_id}")
def sandbox_get(run_id: str, uid: str = Depends(user_id)) -> dict[str, Any]:
    with session_scope() as db:
        r = db.get(SandboxRun, run_id)
        if r is None or r.user_id != uid:
            raise AppError("not_found", "Run not found.", status=404)
        repo = db.get(Repository, r.repository_id)
        return {"id": r.id, "repository": repo.full_name, "commit_sha": r.commit_sha, "command": r.command, "image": r.image, "status": r.status,
                "exit_code": r.exit_code, "tests_run": r.tests_run, "passed": r.passed, "failed": r.failed, "skipped": r.skipped, "duration_ms": r.duration_ms,
                "output_excerpt": r.output_excerpt, "runtime_version": r.runtime_version, "constraints": r.constraints, "error": r.error,
                "consent_at": r.consent_at, "created_at": r.created_at, "finished_at": r.finished_at}


# ------------------------------------------------------------------ passports


class PassportBody(BaseModel):
    assessment_id: str
    row_ids: list[str] = Field(min_length=1, max_length=40)
    title: str = Field(min_length=3, max_length=300)
    visibility: str = Field(default="private", pattern="^(public|private)$")
    include_permalinks: bool = True
    include_display_name: bool = False
    expires_days: int | None = Field(default=None, ge=1, le=730)


@router.post("/passports")
def create_passport(body: PassportBody, uid: str = Depends(user_id)) -> dict[str, Any]:
    return passport.create_passport(uid, body.assessment_id, body.row_ids, title=body.title, visibility=body.visibility,
                                    include_permalinks=body.include_permalinks, include_display_name=body.include_display_name, expires_days=body.expires_days)


@router.get("/passports")
def list_passports(uid: str = Depends(user_id)) -> list[dict[str, Any]]:
    with session_scope() as db:
        rows = db.execute(select(SkillPassport).where(SkillPassport.user_id == uid).order_by(SkillPassport.created_at.desc())).scalars().all()
        return [{"artifact_id": p.artifact_id, "title": p.title, "visibility": p.visibility, "expires_at": p.expires_at, "revoked_at": p.revoked_at,
                 "created_at": p.created_at, "skills": len(p.payload.get("skills", [])), "key_id": p.key_id} for p in rows]


class RevokeBody(BaseModel):
    reason: str = Field(default="", max_length=500)


@router.post("/passports/{artifact_id}/revoke")
def revoke(artifact_id: str, body: RevokeBody, uid: str = Depends(user_id)) -> dict[str, Any]:
    passport.revoke(uid, artifact_id, body.reason)
    return passport.verify(artifact_id)


@router.get("/passports/{artifact_id}")
def owner_passport(artifact_id: str, uid: str = Depends(user_id)) -> dict[str, Any]:
    with session_scope() as db:
        p = db.execute(select(SkillPassport).where(SkillPassport.artifact_id == artifact_id)).scalar_one_or_none()
        if p is None or p.user_id != uid:
            raise AppError("not_found", "Passport not found.", status=404)
        doc = {"payload": p.payload, "signature": p.signature, "key_id": p.key_id, "alg": "Ed25519", "visibility": p.visibility, "title": p.title}
    return {**doc, "verification": passport.verify(artifact_id)}


public = APIRouter(prefix="/api/public", tags=["public"])


@public.get("/passports/{artifact_id}")
def public_passport(artifact_id: str) -> dict[str, Any]:
    with session_scope() as db:
        p = db.execute(select(SkillPassport).where(SkillPassport.artifact_id == artifact_id)).scalar_one_or_none()
        if p is None or p.visibility != "public":
            raise AppError("not_found", "Passport not found or not public.", status=404)
        doc = {"payload": p.payload, "signature": p.signature, "key_id": p.key_id, "alg": "Ed25519", "title": p.title}
    return {**doc, "verification": passport.verify(artifact_id)}


class VerifyBody(BaseModel):
    payload: dict[str, Any]
    signature: str


@public.post("/passports/{artifact_id}/verify")
def public_verify(artifact_id: str, body: VerifyBody) -> dict[str, Any]:
    """Verify a downloaded passport JSON (payload + signature) — detects tampering."""
    return passport.verify(artifact_id, body.payload, body.signature)


@public.get("/passport-keys")
def keys() -> dict[str, Any]:
    return {"keys": passport.public_keys(), "canonicalization": "JSON, sorted keys, separators (',', ':'), UTF-8"}


# ------------------------------------------------------------------ insights


@router.get("/insights/market")
def market(search_run_ids: str | None = None, uid: str = Depends(user_id)) -> dict[str, Any]:
    return insights.market_demand(uid, [x for x in (search_run_ids or "").split(",") if x] or None)


@router.get("/insights/roadmap/{job_id}")
def roadmap(job_id: str, uid: str = Depends(user_id)) -> dict[str, Any]:
    return insights.roadmap(uid, job_id)


@router.get("/insights/freshness")
def freshness(status: str = "open", uid: str = Depends(user_id)) -> list[dict[str, Any]]:
    with session_scope() as db:
        q = select(EvidenceFreshnessEvent).where(EvidenceFreshnessEvent.user_id == uid)
        if status != "all":
            q = q.where(EvidenceFreshnessEvent.status == status)
        return [{"id": e.id, "kind": e.kind, "source_type": e.source_type, "source_id": e.source_id, "reason": e.reason, "trigger_ref": e.trigger_ref,
                 "detail": e.detail, "status": e.status, "created_at": e.created_at}
                for e in db.execute(q.order_by(EvidenceFreshnessEvent.created_at.desc()).limit(200)).scalars()]


class FreshUpdate(BaseModel):
    status: str = Field(pattern="^(resolved|dismissed|open)$")


@router.post("/insights/freshness/{event_id}")
def update_freshness(event_id: str, body: FreshUpdate, uid: str = Depends(user_id)) -> dict[str, Any]:
    with session_scope() as db:
        e = db.get(EvidenceFreshnessEvent, event_id)
        if e is None or e.user_id != uid:
            raise AppError("not_found", "Event not found.", status=404)
        e.status = body.status
    return {"ok": True}
