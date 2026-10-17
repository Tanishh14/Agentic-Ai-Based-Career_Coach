"""Test-only helpers to create jobs and scopes in the per-test SQLite database."""
from __future__ import annotations

import asyncio

from sqlalchemy import select

from backend.app.agents.orchestrator import AgentRun
from backend.app.database import session_scope
from backend.app.models import AnalysisScope, Assessment, AssessmentDiff, AssessmentRow, EvidenceRecord, Repository
from backend.app.services import jobs as jobs_svc
from backend.app.services.integrations_store import current_user_id
from backend.app.services.reassessment import handle_repo_change

JD = """Backend Engineering Intern

Requirements:
- Experience building REST APIs with Python
- Writing SQL queries against a relational database
- Unit tests for your code
- Deploying services with Docker

Nice to have:
- Experience with Kubernetes is a plus
"""


def run(coro):
    return asyncio.run(coro)


def make_job(text: str = JD) -> str:
    uid = current_user_id()

    async def _go():
        r = AgentRun(uid, "test")
        pid = await jobs_svc.ingest(uid, method="user_paste", text=text, source_url="https://example.org/jobs/123", meta={"title": "Backend Intern"},
                                    warnings=[], run=r)
        r.finish()
        return pid

    return run(_go())


def make_scope(full_name: str = "student/payments-api", include: list[str] | None = None) -> str:
    uid = current_user_id()
    with session_scope() as db:
        repo = db.execute(select(Repository).where(Repository.full_name == full_name)).scalar_one_or_none()
        if repo is None:
            repo = Repository(user_id=uid, github_id="42", owner=full_name.split("/")[0], name=full_name.split("/")[1], full_name=full_name,
                              default_branch="main", html_url=f"https://github.com/{full_name}")
            db.add(repo)
            db.flush()
        prev = db.execute(select(AnalysisScope).where(AnalysisScope.repository_id == repo.id).order_by(AnalysisScope.version.desc())).scalars().first()
        sc = AnalysisScope(user_id=uid, repository_id=repo.id, version=(prev.version + 1) if prev else 1, branch="main",
                           include_patterns=include or [], exclude_patterns=[], active=True)
        db.add(sc)
        db.flush()
        return sc.id


def scan(scope_id: str, trigger: str = "poll") -> dict:
    return run(handle_repo_change(scope_id, trigger))


def latest(job_id: str) -> tuple[Assessment, dict[str, AssessmentRow]]:
    with session_scope() as db:
        a = db.execute(select(Assessment).where(Assessment.job_posting_id == job_id).order_by(Assessment.version.desc())).scalars().first()
        rows = {r.skill_key: r for r in db.execute(select(AssessmentRow).where(AssessmentRow.assessment_id == a.id)).scalars()}
        for r in rows.values():
            db.expunge(r)
        db.expunge(a)
        return a, rows


def latest_diff() -> AssessmentDiff | None:
    with session_scope() as db:
        d = db.execute(select(AssessmentDiff).order_by(AssessmentDiff.created_at.desc())).scalars().first()
        if d:
            db.expunge(d)
        return d


def current_evidence(scope_id: str) -> list[EvidenceRecord]:
    with session_scope() as db:
        out = db.execute(select(EvidenceRecord).where(EvidenceRecord.scope_id == scope_id, EvidenceRecord.status == "current")).scalars().all()
        for e in out:
            db.expunge(e)
        return out
