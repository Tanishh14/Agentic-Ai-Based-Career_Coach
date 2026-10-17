"""Per-job leaderboard across students.

The same real posting is matched across accounts by canonical URL (or by text hash for pasted
postings). Each student's combined score is a transparent weighted mix of signals the app
already stores — never invented:

  combined = 0.60 × evidence coverage (latest assessment, 0–100)
           + 0.25 × mock-interview average (completed sessions for this job, 0–100)
           + 0.15 × code-defense average (understanding evidence, 0–100)

A missing component counts as 0 and is shown as "not attempted". This is a readiness ranking
for coaching, not a hiring prediction.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import inspect as sa_inspect, or_, select

from ..database import session_scope
from ..errors import AppError
from ..models import (Assessment, AssessmentRow, InterviewSession, JobPosting, Requirement, UnderstandingEvidence, User)

WEIGHTS = {"evidence": 0.60, "interview": 0.25, "defense": 0.15}
FORMULA = "combined = 0.60 × evidence coverage + 0.25 × mock-interview average + 0.15 × code-defense average (each 0–100; not attempted = 0)"


def _keys(p: JobPosting) -> set[str]:
    keys = {f"hash:{p.content_hash}"}
    if p.canonical_url:
        keys.add(f"url:{p.canonical_url}")
    if p.external_id:
        keys.add(f"ext:{p.provider}:{p.external_id}")
    return keys


def _group_filter(jp: JobPosting):
    """Same real posting = same URL, same provider job id, or identical text."""
    conds = [JobPosting.content_hash == jp.content_hash]
    if jp.canonical_url:
        conds.append(JobPosting.canonical_url == jp.canonical_url)
    if jp.external_id:
        conds.append((JobPosting.provider == jp.provider) & (JobPosting.external_id == jp.external_id))
    return or_(*conds)


def _visible(u: User) -> bool:
    return bool(u.username) and (u.settings or {}).get("show_on_leaderboard", True)


def leaderboard(job_posting_id: str, viewer_id: str) -> dict[str, Any]:
    with session_scope() as db:
        jp = db.get(JobPosting, job_posting_id)
        if jp is None or jp.user_id != viewer_id:
            raise AppError("not_found", "Job posting not found.", status=404)
        postings = db.execute(select(JobPosting).where(_group_filter(jp), JobPosting.status == "analyzed")).scalars().all()
        rows: list[dict[str, Any]] = []
        for p in postings:
            u = db.get(User, p.user_id)
            if u is None or (not _visible(u) and u.id != viewer_id):
                continue
            a = db.execute(select(Assessment).where(Assessment.job_posting_id == p.id).order_by(Assessment.version.desc()).limit(1)).scalar_one_or_none()
            coverage = (a.summary or {}).get("coverage_indicator") if a else None
            demonstrated = must_total = must_demo = 0
            repos: list[str] = []
            if a:
                ars = db.execute(select(AssessmentRow, Requirement).join(Requirement, AssessmentRow.requirement_id == Requirement.id)
                                 .where(AssessmentRow.assessment_id == a.id)).all()
                demonstrated = sum(1 for r, _ in ars if r.verdict == "DEMONSTRATED")
                must_total = sum(1 for _, q in ars if q.priority == "must_have" and not q.out_of_scope)
                must_demo = sum(1 for r, q in ars if q.priority == "must_have" and not q.out_of_scope and r.verdict == "DEMONSTRATED")
                repos = [s["repo"] for s in (a.scopes or [])]
            sessions = db.execute(select(InterviewSession).where(InterviewSession.user_id == u.id, InterviewSession.kind == "mock",
                                                                 InterviewSession.status == "completed",
                                                                 InterviewSession.job_posting_id == p.id)
                                  .order_by(InterviewSession.ended_at.desc()).limit(3)).scalars().all()
            iscores = [s.report.get("overall") for s in sessions if s.report and s.report.get("overall") is not None]
            interview = round(sum(iscores) / len(iscores), 1) if iscores else None
            ud = db.execute(select(UnderstandingEvidence).where(UnderstandingEvidence.user_id == u.id,
                                                                UnderstandingEvidence.evidence_type == "UNDERSTANDING_EVIDENCE")
                            .order_by(UnderstandingEvidence.created_at.desc()).limit(10)).scalars().all()
            defense = round(sum(x.rubric_score for x in ud) / len(ud), 1) if ud else None
            combined = round(WEIGHTS["evidence"] * (coverage or 0) + WEIGHTS["interview"] * (interview or 0) + WEIGHTS["defense"] * (defense or 0), 1)
            rows.append({"user_id": u.id, "name": u.display_name or u.username, "is_you": u.id == viewer_id, "combined": combined,
                         "evidence_coverage": coverage, "interview_avg": interview, "interviews_completed": len(iscores),
                         "defense_avg": defense, "defense_answers": len(ud), "demonstrated": demonstrated,
                         "must_have_demonstrated": must_demo, "must_have_total": must_total, "repositories": repos,
                         "assessment_version": a.version if a else None, "assessed_at": a.created_at if a else None,
                         "hidden": not _visible(u)})
        best: dict[str, dict[str, Any]] = {}
        for r in rows:  # a student with several copies of the same posting counts once (best copy)
            cur = best.get(r["user_id"])
            if cur is None or r["combined"] > cur["combined"]:
                best[r["user_id"]] = r
        rows = list(best.values())
        rows.sort(key=lambda r: (-r["combined"], -(r["evidence_coverage"] or 0), r["name"].lower()))
        for i, r in enumerate(rows, 1):
            r["rank"] = i
        return {"job": {"id": jp.id, "title": jp.title, "company": jp.company, "source_url": jp.source_url}, "participants": len(rows),
                "weights": WEIGHTS, "formula": FORMULA, "rows": rows,
                "disclaimer": "A coaching ranking built from each student's own verified evidence and practice — not a hiring prediction."}


def shared_jobs(viewer_id: str) -> list[dict[str, Any]]:
    """Real postings other students are working on, so a student can join the same leaderboard."""
    with session_scope() as db:
        postings = db.execute(select(JobPosting).where(JobPosting.status == "analyzed")).scalars().all()
        groups: list[dict[str, Any]] = []
        for p in postings:
            u = db.get(User, p.user_id)
            if u is None or not u.username:
                continue
            k = _keys(p)
            g = next((x for x in groups if x["_keys"] & k), None)
            if g is None:
                g = {"key": sorted(k)[0], "_keys": set(), "_users": set(), "title": p.title, "company": p.company, "source_url": p.source_url,
                     "students": 0, "your_posting_id": None, "sample_posting_id": p.id}
                groups.append(g)
            g["_keys"] |= k
            g["_users"].add(p.user_id)
            g["students"] = len(g["_users"])
            if p.user_id == viewer_id:
                g["your_posting_id"] = p.id
        out = [{k: v for k, v in g.items() if not k.startswith("_")} for g in groups]
        return sorted(out, key=lambda g: (-g["students"], (g["title"] or "").lower()))


def join(posting_id: str, viewer_id: str) -> str:
    """Copy another student's posting (text + verified requirements) into your account."""
    with session_scope() as db:
        src = db.get(JobPosting, posting_id)
        if src is None or src.status != "analyzed":
            raise AppError("not_found", "Job posting not found.", status=404)
        mine = db.execute(select(JobPosting).where(JobPosting.user_id == viewer_id, _group_filter(src))).scalars().first()
        if mine is not None:
            return mine.id
        fields = {a.key: getattr(src, a.key) for a in sa_inspect(JobPosting).column_attrs
                  if a.key not in ("id", "user_id", "search_run_id", "is_tracked", "created_at")}
        copy = JobPosting(user_id=viewer_id, **fields)
        db.add(copy)
        db.flush()
        for r in db.execute(select(Requirement).where(Requirement.posting_id == src.id)).scalars():
            db.add(Requirement(**{a.key: getattr(r, a.key) for a in sa_inspect(Requirement).column_attrs if a.key not in ("id", "posting_id", "created_at")},
                               posting_id=copy.id))
        return copy.id


__all__ = ["leaderboard", "shared_jobs", "join", "FORMULA"]
