"""Market-demand weighting, readiness ranking of discovered jobs, and the counterfactual gap roadmap."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from sqlalchemy import select

from ..agents.jd_extractor import deterministic_extract
from ..analyzers.taxonomy import get_skill, skill_name
from ..database import session_scope
from ..models import (AnalysisScope, Assessment, AssessmentRow, EvidenceRecord, GapTask, JobPosting, JobSearchRun, MarketObservation,
                      Requirement)
from .assessments import PRIORITY_WEIGHT, VERDICT_CREDIT, compute_row

EFFORT = {"automated_testing": ("S", 1), "ci_cd": ("S", 1), "containerization": ("S", 1), "sql": ("M", 2), "api_development": ("M", 2),
          "deployment": ("M", 2), "auth_security": ("M", 2), "caching": ("M", 2), "observability": ("S", 1), "concurrency": ("M", 2),
          "kubernetes": ("L", 3), "terraform": ("L", 3), "system_design": ("L", 3), "machine_learning": ("L", 3)}
ARTIFACTS = {
    "automated_testing": "test modules whose tests call your code and assert on results; a CI step that runs them",
    "sql": "a persistence layer that executes parameterised SQL/ORM queries from request handlers, plus a migration",
    "api_development": "route handlers with validation, error responses and tests",
    "ci_cd": "a workflow that installs, tests and builds on every push",
    "containerization": "a Dockerfile (non-root, CMD) and optionally docker-compose for dependencies",
    "deployment": "platform config or a deploy workflow step and a health endpoint",
    "auth_security": "password hashing / token verification and a protected route with tests",
}


def current_evidence_by_skill(user_id: str) -> tuple[dict[str, list[EvidenceRecord]], list[dict]]:
    from .assessments import _scopes_info

    with session_scope() as db:
        info, scope_ids, _ = _scopes_info(db, user_id)
        by: dict[str, list[EvidenceRecord]] = defaultdict(list)
        if scope_ids:
            for e in db.execute(select(EvidenceRecord).where(EvidenceRecord.scope_id.in_(scope_ids), EvidenceRecord.status == "current")).scalars():
                db.expunge(e)
                by[e.skill_key].append(e)
    return by, info


def readiness_for_text(text: str, ev: dict[str, list[EvidenceRecord]], scopes: list[dict]) -> dict[str, Any]:
    reqs = deterministic_extract(text)
    counts: dict[str, int] = defaultdict(int)
    w_total = w_got = 0.0
    items = []
    for r in reqs:
        row = compute_row(r.skill_key, ev.get(r.skill_key, []), [], scopes)
        counts[row["verdict"]] += 1
        w = PRIORITY_WEIGHT.get(r.priority, 2)
        w_total += w
        w_got += w * VERDICT_CREDIT.get(row["verdict"], 0)
        items.append({"skill_key": r.skill_key, "skill": r.display_name, "priority": r.priority, "verdict": row["verdict"]})
    return {"coverage_indicator": round(100 * w_got / w_total, 1) if w_total else None, "counts": dict(counts), "requirements": items,
            "method": "deterministic taxonomy extraction of this listing vs your current evidence; same weights as the gap matrix; not a hiring probability"}


def market_demand(user_id: str, search_run_ids: list[str] | None = None) -> dict[str, Any]:
    with session_scope() as db:
        q = select(MarketObservation, JobPosting).join(JobPosting, MarketObservation.posting_id == JobPosting.id).where(MarketObservation.user_id == user_id)
        if search_run_ids:
            q = q.where(MarketObservation.search_run_id.in_(search_run_ids))
        rows = db.execute(q).all()
        runs = db.execute(select(JobSearchRun).where(JobSearchRun.user_id == user_id) if not search_run_ids else
                          select(JobSearchRun).where(JobSearchRun.id.in_(search_run_ids))).scalars().all()
    postings = {jp.id: jp for _, jp in rows}
    n = len(postings)
    skill_counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    providers: dict[str, int] = defaultdict(int)
    dates = sorted(jp.posted_at for jp in postings.values() if jp.posted_at)
    for mo, jp in rows:
        skill_counts[mo.skill_key][mo.priority] += 1
    for jp in postings.values():
        providers[jp.provider] += 1
    skills = []
    for k, pr in skill_counts.items():
        total = sum(pr.values())
        skills.append({"skill_key": k, "skill": skill_name(k), "postings": total, "share": round(total / n, 3) if n else 0, "by_priority": dict(pr)})
    skills.sort(key=lambda x: -x["postings"])
    warn = None
    if n < 20:
        warn = f"Small sample ({n} postings): treat frequencies as anecdotal, not a labour-market forecast."
    return {"sample_size": n, "providers": dict(providers), "date_range": [dates[0], dates[-1]] if dates else None,
            "search_runs": [{"id": r.id, "filters": r.filters, "results": r.result_count, "duplicates": r.duplicate_count, "at": r.created_at} for r in runs],
            "skills": skills[:40], "warning": warn,
            "method": "Skill frequency over the selected real postings (deterministic taxonomy extraction). Biased by your queries and providers."}


def roadmap(user_id: str, job_posting_id: str) -> dict[str, Any]:
    market = market_demand(user_id)
    share = {s["skill_key"]: s["share"] for s in market["skills"]}
    with session_scope() as db:
        a = db.execute(select(Assessment).where(Assessment.job_posting_id == job_posting_id, Assessment.user_id == user_id)
                       .order_by(Assessment.version.desc()).limit(1)).scalar_one_or_none()
        if a is None:
            return {"items": [], "note": "Run an assessment for this job first."}
        rows = db.execute(select(AssessmentRow, Requirement).join(Requirement, AssessmentRow.requirement_id == Requirement.id)
                          .where(AssessmentRow.assessment_id == a.id)).all()
        tasks = {t.assessment_row_id: t for t in db.execute(select(GapTask).where(GapTask.job_posting_id == job_posting_id)).scalars()}
        items = []
        for r, req in rows:
            if r.verdict == "DEMONSTRATED" or req.out_of_scope:
                continue
            gap = 1.0 if r.verdict in ("NOT_DEMONSTRATED_IN_SCOPE", "CLAIMED_ONLY") else 0.5
            eff_label, eff = EFFORT.get(r.skill_key, ("M", 2))
            m = share.get(r.skill_key, 0.0)
            score = round(PRIORITY_WEIGHT.get(req.priority, 2) * gap * (1 + m) / eff, 3)
            sk = get_skill(r.skill_key)
            detectors = sk.detector if sk and sk.detector else None
            t = tasks.get(r.id)
            items.append({
                "row_id": r.id, "skill_key": r.skill_key, "skill": req.display_name, "priority": req.priority, "verdict": r.verdict, "score": score,
                "effort": eff_label, "market_share": m,
                "supports_requirement": req.quote, "source_url": req.source_url,
                "missing_evidence": r.rationale,
                "demonstrating_artifacts": ARTIFACTS.get(r.skill_key, f"code in a selected repository that uses {req.display_name} beyond a dependency declaration"),
                "automated_checks": (f"On your next push the {detectors} detector and the evidence critic re-run on changed files; the row is re-evaluated automatically."
                                     if detectors else "No automated detector exists for this skill — it cannot be verified from code."),
                "recommendation": r.recommendation, "task": {"id": t.id, "status": t.status, "issue": t.github_issue_url} if t else None,
            })
    items.sort(key=lambda x: -x["score"])
    return {"assessment_id": a.id, "assessment_version": a.version, "items": items,
            "formula": "score = priority_weight × gap (1 missing, 0.5 partial) × (1 + market share in your searched postings) ÷ effort (S=1, M=2, L=3)",
            "disclaimer": "Effort is a rough estimate. Completing recommendations is not a skill until real code demonstrates it, and nothing here guarantees a job."}
