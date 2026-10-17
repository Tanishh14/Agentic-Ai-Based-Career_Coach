"""Job ingestion, requirements, discovery and readiness ranking."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, Form, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select

from ..agents.orchestrator import AgentRun
from ..database import session_scope
from ..errors import AppError
from ..integrations.job_sources import PROVIDER_INFO, SearchFilters
from ..models import Assessment, JobPosting, JobSearchRun, Requirement
from ..services import jobs as jobs_svc
from ..services.assessments import create_assessment, requirement_dict
from ..services.documents import extract_text
from ..services.insights import current_evidence_by_skill, readiness_for_text
from .deps import user_id

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


def posting_dict(jp: JobPosting, with_text: bool = False) -> dict[str, Any]:
    d = {"id": jp.id, "source_url": jp.source_url, "canonical_url": jp.canonical_url, "platform": jp.platform, "provider": jp.provider,
         "ingestion_method": jp.ingestion_method, "external_id": jp.external_id, "company": jp.company, "title": jp.title, "location": jp.location,
         "employment_type": jp.employment_type, "posted_at": jp.posted_at, "content_hash": jp.content_hash, "retrieved_at": jp.retrieved_at,
         "extraction_warning": jp.extraction_warning, "status": jp.status, "is_tracked": jp.is_tracked, "description_is_partial": jp.description_is_partial,
         "meta": jp.meta or {}, "search_run_id": jp.search_run_id, "created_at": jp.created_at, "text_length": len(jp.raw_text or "")}
    if with_text:
        d["raw_text"] = jp.raw_text
    return d


async def _after_ingest(uid: str, posting_id: str, run: AgentRun) -> dict[str, Any]:
    aid = create_assessment(posting_id, "requirements_changed", agent_run_id=run.id)
    if aid:
        run.record("tool_call", tool="reassess_affected_skills", input_summary=f"job {posting_id[:8]}", result_summary=f"assessment {aid[:8]} created", entity_ids=[aid])
    run.finish("succeeded", f"job {posting_id[:8]} ingested and analysed")
    return get_job(posting_id, uid)


class UrlIngest(BaseModel):
    url: str = Field(min_length=8, max_length=2000)
    track: bool = True


@router.post("/ingest/url")
async def ingest_url(body: UrlIngest, uid: str = Depends(user_id)) -> dict[str, Any]:
    run = AgentRun(uid, "job_ingest_url")
    try:
        text, meta, warnings, method = await run.tool("fetch_job_posting", body.url, jobs_svc.fetch_posting, body.url, run,
                                                      summarize=lambda r: f"{len(r[0])} chars via {r[3]}{' with warnings' if r[2] else ''}")
        pid = await jobs_svc.ingest(uid, method=method, text=text, source_url=meta.get("absolute_url") or body.url, meta=meta, warnings=warnings,
                                    run=run, track=body.track)
        return await _after_ingest(uid, pid, run)
    except AppError as e:
        run.finish("failed", error=e.message)
        if e.code in ("blocked_by_robots", "login_required", "fetch_failed", "not_found", "unsupported_type", "rate_limited"):
            e.detail = {**(e.detail or {}), "fallback": "paste_or_upload", "source_url": body.url}
        raise


class TextIngest(BaseModel):
    text: str = Field(min_length=40, max_length=200_000)
    source_url: str | None = Field(default=None, max_length=2000)
    company: str | None = Field(default=None, max_length=300)
    title: str | None = Field(default=None, max_length=300)
    location: str | None = Field(default=None, max_length=300)
    track: bool = True


@router.post("/ingest/text")
async def ingest_text(body: TextIngest, uid: str = Depends(user_id)) -> dict[str, Any]:
    run = AgentRun(uid, "job_ingest_paste")
    meta = {k: v for k, v in {"company": body.company, "title": body.title, "location": body.location}.items() if v}
    if body.source_url:
        from ..integrations.job_sources import platform_of

        meta["provider"] = platform_of(body.source_url)
    try:
        pid = await jobs_svc.ingest(uid, method="user_paste", text=body.text, source_url=body.source_url, meta=meta, warnings=[], run=run, track=body.track)
        return await _after_ingest(uid, pid, run)
    except AppError as e:
        run.finish("failed", error=e.message)
        raise


@router.post("/ingest/file")
async def ingest_file(file: UploadFile = File(...), source_url: str | None = Form(default=None), company: str | None = Form(default=None),
                      title: str | None = Form(default=None), uid: str = Depends(user_id)) -> dict[str, Any]:
    data = await file.read()
    doc = extract_text(file.filename or "upload", file.content_type, data)
    run = AgentRun(uid, "job_ingest_upload")
    meta = {k: v for k, v in {"company": company, "title": title}.items() if v}
    try:
        pid = await jobs_svc.ingest(uid, method="upload", text=doc.text, source_url=source_url or None, meta={**meta, "filename": file.filename},
                                    warnings=[doc.warning] if doc.warning else [], run=run)
        return await _after_ingest(uid, pid, run)
    except AppError as e:
        run.finish("failed", error=e.message)
        raise


@router.get("")
def list_jobs(status: str | None = None, uid: str = Depends(user_id)) -> list[dict[str, Any]]:
    with session_scope() as db:
        q = select(JobPosting).where(JobPosting.user_id == uid)
        if status:
            q = q.where(JobPosting.status == status)
        rows = db.execute(q.order_by(JobPosting.is_tracked.desc(), JobPosting.created_at.desc()).limit(500)).scalars().all()
        out = []
        for jp in rows:
            d = posting_dict(jp)
            if jp.status == "analyzed":
                a = db.execute(select(Assessment).where(Assessment.job_posting_id == jp.id).order_by(Assessment.version.desc()).limit(1)).scalar_one_or_none()
                d["latest_assessment"] = {"id": a.id, "version": a.version, "coverage": (a.summary or {}).get("coverage_indicator"),
                                          "counts": (a.summary or {}).get("counts"), "created_at": a.created_at} if a else None
            out.append(d)
        return out


@router.get("/{job_id}")
def get_job(job_id: str, uid: str = Depends(user_id)) -> dict[str, Any]:
    with session_scope() as db:
        jp = db.get(JobPosting, job_id)
        if jp is None or jp.user_id != uid:
            raise AppError("not_found", "Job posting not found.", status=404)
        reqs = db.execute(select(Requirement).where(Requirement.posting_id == job_id)).scalars().all()
        d = posting_dict(jp, with_text=True)
        prio = {"must_have": 0, "unclear": 1, "preferred": 2}
        d["requirements"] = [requirement_dict(r) for r in sorted(reqs, key=lambda r: (prio.get(r.priority, 3), r.char_start))]
        return d


@router.post("/{job_id}/track")
def track(job_id: str, uid: str = Depends(user_id)) -> dict[str, Any]:
    jobs_svc.set_tracked(uid, job_id)
    with session_scope() as db:
        has = db.execute(select(Assessment.id).where(Assessment.job_posting_id == job_id)).first()
    if not has:
        create_assessment(job_id, "target_selected")
    return {"tracked": job_id}


@router.post("/{job_id}/analyze")
async def analyze_listing(job_id: str, uid: str = Depends(user_id)) -> dict[str, Any]:
    """Deep analysis of a discovered listing (requirements extraction + assessment)."""
    with session_scope() as db:
        jp = db.get(JobPosting, job_id)
        if jp is None or jp.user_id != uid:
            raise AppError("not_found", "Job posting not found.", status=404)
        partial = jp.description_is_partial
    run = AgentRun(uid, "job_deep_analysis")
    try:
        await jobs_svc.extract_and_store(uid, job_id, run)
        jobs_svc.set_tracked(uid, job_id)
        res = await _after_ingest(uid, job_id, run)
        if partial:
            res["warning"] = "This provider only supplies a description snippet; paste the full description for a complete analysis."
        return res
    except AppError as e:
        run.finish("failed", error=e.message)
        raise


@router.post("/{job_id}/reextract")
async def reextract(job_id: str, uid: str = Depends(user_id)) -> dict[str, Any]:
    run = AgentRun(uid, "requirements_reextract")
    await jobs_svc.extract_and_store(uid, job_id, run)
    return await _after_ingest(uid, job_id, run)


class OutOfScope(BaseModel):
    out_of_scope: bool


@router.post("/requirements/{req_id}/scope")
def set_req_scope(req_id: str, body: OutOfScope, uid: str = Depends(user_id)) -> dict[str, Any]:
    with session_scope() as db:
        r = db.get(Requirement, req_id)
        jp = db.get(JobPosting, r.posting_id) if r else None
        if r is None or jp.user_id != uid:
            raise AppError("not_found", "Requirement not found.", status=404)
        r.out_of_scope = body.out_of_scope
        job_id = jp.id
    aid = create_assessment(job_id, "requirements_changed")
    return {"assessment_id": aid}


@router.delete("/{job_id}")
def delete_job(job_id: str, uid: str = Depends(user_id)) -> dict[str, Any]:
    with session_scope() as db:
        jp = db.get(JobPosting, job_id)
        if jp is None or jp.user_id != uid:
            raise AppError("not_found", "Job posting not found.", status=404)
        db.delete(jp)
    return {"deleted": True}


# ------------------------------------------------------------------ discovery

disc = APIRouter(prefix="/api/discovery", tags=["discovery"])


@disc.get("/providers")
def providers() -> dict[str, Any]:
    return PROVIDER_INFO


class SearchBody(BaseModel):
    query: str = Field(min_length=2, max_length=120)
    location: str | None = Field(default=None, max_length=120)
    level: str | None = Field(default=None, pattern="^(internship|entry|any)?$")
    remote_only: bool = False
    providers: list[str] = Field(default_factory=lambda: ["remotive", "arbeitnow", "remoteok", "greenhouse", "lever", "adzuna", "serpapi"])
    greenhouse_boards: list[str] = Field(default_factory=list, max_length=10)
    lever_sites: list[str] = Field(default_factory=list, max_length=10)


@disc.post("/search")
async def search(body: SearchBody, uid: str = Depends(user_id)) -> dict[str, Any]:
    f = SearchFilters(query=body.query, location=body.location or None, level=body.level if body.level != "any" else None, remote_only=body.remote_only,
                      greenhouse_boards=body.greenhouse_boards, lever_sites=body.lever_sites)
    res = await jobs_svc.discover(uid, f, [p for p in body.providers if p in PROVIDER_INFO])
    return {**res, "results": ranked_results(uid, res["posting_ids"])}


def ranked_results(uid: str, posting_ids: list[str]) -> list[dict[str, Any]]:
    ev, scopes = current_evidence_by_skill(uid)
    with session_scope() as db:
        rows = db.execute(select(JobPosting).where(JobPosting.id.in_(posting_ids), JobPosting.user_id == uid)).scalars().all()
        out = []
        for jp in rows:
            d = posting_dict(jp)
            d["readiness"] = readiness_for_text(jp.raw_text, ev, scopes)
            d["snippet"] = (jp.raw_text or "")[:400]
            out.append(d)
    out.sort(key=lambda d: -(d["readiness"]["coverage_indicator"] or 0))
    return out


@disc.get("/runs")
def runs(uid: str = Depends(user_id)) -> list[dict[str, Any]]:
    with session_scope() as db:
        rs = db.execute(select(JobSearchRun).where(JobSearchRun.user_id == uid).order_by(JobSearchRun.created_at.desc()).limit(30)).scalars()
        return [{"id": r.id, "filters": r.filters, "providers": r.providers, "result_count": r.result_count, "duplicate_count": r.duplicate_count,
                 "errors": r.errors, "created_at": r.created_at} for r in rs]


@disc.get("/runs/{run_id}")
def run_results(run_id: str, uid: str = Depends(user_id)) -> dict[str, Any]:
    with session_scope() as db:
        r = db.get(JobSearchRun, run_id)
        if r is None or r.user_id != uid:
            raise AppError("not_found", "Search run not found.", status=404)
        ids = [i for (i,) in db.execute(select(JobPosting.id).where(JobPosting.search_run_id == run_id))]
        meta = {"id": r.id, "filters": r.filters, "providers": r.providers, "result_count": r.result_count, "duplicate_count": r.duplicate_count,
                "created_at": r.created_at}
    return {**meta, "results": ranked_results(uid, ids)}
