"""Assessments (gap matrix), version history, diffs and side-by-side comparison."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select

from ..database import session_scope
from ..errors import AppError
from ..models import Assessment, AssessmentDiff, JobPosting
from ..services.assessments import create_assessment, serialize_assessment
from .deps import user_id

router = APIRouter(prefix="/api/assessments", tags=["assessments"])


def _job(db, uid: str, job_id: str) -> JobPosting:
    jp = db.get(JobPosting, job_id)
    if jp is None or jp.user_id != uid:
        raise AppError("not_found", "Job posting not found.", status=404)
    return jp


@router.get("/job/{job_id}/latest")
def latest(job_id: str, uid: str = Depends(user_id)) -> dict[str, Any] | None:
    with session_scope() as db:
        _job(db, uid, job_id)
        a = db.execute(select(Assessment).where(Assessment.job_posting_id == job_id).order_by(Assessment.version.desc()).limit(1)).scalar_one_or_none()
        return serialize_assessment(db, a) if a else None


@router.get("/job/{job_id}/versions")
def versions(job_id: str, uid: str = Depends(user_id)) -> list[dict[str, Any]]:
    with session_scope() as db:
        _job(db, uid, job_id)
        rows = db.execute(select(Assessment).where(Assessment.job_posting_id == job_id).order_by(Assessment.version.desc())).scalars()
        return [{"id": a.id, "version": a.version, "trigger_type": a.trigger_type, "trigger_ref": a.trigger_ref, "trigger_commit_sha": a.trigger_commit_sha,
                 "summary": a.summary, "scopes": a.scopes, "created_at": a.created_at, "agent_run_id": a.agent_run_id} for a in rows]


@router.post("/job/{job_id}/recompute")
def recompute(job_id: str, uid: str = Depends(user_id)) -> dict[str, Any]:
    with session_scope() as db:
        _job(db, uid, job_id)
    return {"assessment_id": create_assessment(job_id, "manual_rescan")}


@router.get("/job/{job_id}/diffs")
def diffs(job_id: str, uid: str = Depends(user_id)) -> list[dict[str, Any]]:
    with session_scope() as db:
        _job(db, uid, job_id)
        ids = [a.id for a in db.execute(select(Assessment).where(Assessment.job_posting_id == job_id)).scalars()]
        versions = {a.id: a.version for a in db.execute(select(Assessment).where(Assessment.id.in_(ids))).scalars()}
        rows = db.execute(select(AssessmentDiff).where(AssessmentDiff.to_assessment_id.in_(ids)).order_by(AssessmentDiff.created_at.desc())).scalars()
        return [{"id": d.id, "from_assessment_id": d.from_assessment_id, "to_assessment_id": d.to_assessment_id, "from_version": versions.get(d.from_assessment_id),
                 "to_version": versions.get(d.to_assessment_id), "trigger_type": d.trigger_type, "trigger_ref": d.trigger_ref, "commit_sha": d.commit_sha,
                 "commit_url": d.commit_url, "changed_paths": d.changed_paths, "affected_skills": d.affected_skills, "row_deltas": d.row_deltas,
                 "created_at": d.created_at} for d in rows]


@router.get("/{assessment_id}")
def get_assessment(assessment_id: str, uid: str = Depends(user_id)) -> dict[str, Any]:
    with session_scope() as db:
        a = db.get(Assessment, assessment_id)
        if a is None or a.user_id != uid:
            raise AppError("not_found", "Assessment not found.", status=404)
        return serialize_assessment(db, a)


@router.get("/compare/{from_id}/{to_id}")
def compare(from_id: str, to_id: str, uid: str = Depends(user_id)) -> dict[str, Any]:
    with session_scope() as db:
        a, b = db.get(Assessment, from_id), db.get(Assessment, to_id)
        if not a or not b or a.user_id != uid or b.user_id != uid or a.job_posting_id != b.job_posting_id:
            raise AppError("not_found", "Assessments not found or not comparable.", status=404)
        sa, sb = serialize_assessment(db, a), serialize_assessment(db, b)
    by_req = {r["requirement"]["id"]: r for r in sa["rows"]}
    rows = []
    for r in sb["rows"]:
        o = by_req.get(r["requirement"]["id"])
        rows.append({"requirement": r["requirement"], "before": o, "after": r,
                     "changed": (o is None) or o["verdict"] != r["verdict"] or {e["id"] for e in o["evidence"]} != {e["id"] for e in r["evidence"]},
                     "carried_unchanged": bool(r.get("carried_from_row_id"))})
    return {"from": {k: sa[k] for k in ("id", "version", "created_at", "trigger_type", "trigger_commit_sha", "scopes", "summary")},
            "to": {k: sb[k] for k in ("id", "version", "created_at", "trigger_type", "trigger_commit_sha", "scopes", "summary")}, "rows": rows}
