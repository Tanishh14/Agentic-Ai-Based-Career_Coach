"""Gap-to-Proof: remediation proposals, user-confirmed GitHub issues, commit-triggered verification.

Acceptance criteria are machine-checkable against evidence produced by the normal webhook/poll
loop. A task is never marked complete from a model assertion, and an issue is never created or
closed without an explicit user confirmation (auto-close only if the user enabled it).
"""
from __future__ import annotations

import fnmatch
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select

from ..analyzers.taxonomy import skill_name
from ..database import session_scope, utcnow
from ..errors import AppError, NotConfigured
from ..integrations.github import GitHubClient
from ..integrations.llm import LLMFailed, complete_json, llm_available, wrap_untrusted
from ..models import (AnalysisScope, Assessment, AssessmentRow, EvidenceRecord, GapTask, JobPosting, Repository, Requirement,
                      ScopeSnapshot, SnapshotFile)
from .events import audit, emit
from .integrations_store import github_token, user_settings

CRITERIA_LIBRARY: dict[str, list[dict[str, Any]]] = {
    "automated_testing": [
        {"type": "accepted_evidence_count", "skill_key": "automated_testing", "source_type": "CODE_TEST", "min_depth": "basic", "min": 2,
         "description": "At least two test functions with meaningful assertions that call your code (no `assert True`)."},
        {"type": "verdict_at_least", "skill_key": "automated_testing", "verdict": "DEMONSTRATED", "description": "Automated testing verdict becomes DEMONSTRATED."},
        {"type": "evidence_detector_present", "detector": "ci_runs_tests", "description": "A CI workflow step runs the test suite.", "optional": True},
    ],
    "sql": [
        {"type": "accepted_evidence_count", "skill_key": "sql", "signal_level": "meaningful_implementation", "min_depth": "intermediate", "min": 1,
         "description": "SQL or ORM queries are executed from application code (not only declared)."},
        {"type": "verdict_at_least", "skill_key": "sql", "verdict": "DEMONSTRATED", "description": "SQL verdict becomes DEMONSTRATED."},
    ],
    "ci_cd": [
        {"type": "accepted_evidence_count", "skill_key": "ci_cd", "min_depth": "intermediate", "min": 1, "description": "A CI workflow with meaningful steps (tests/build)."},
        {"type": "evidence_detector_present", "detector": "ci_runs_tests", "description": "The workflow runs the tests."},
    ],
    "containerization": [
        {"type": "accepted_evidence_count", "skill_key": "containerization", "min_depth": "intermediate", "min": 1, "description": "A buildable Dockerfile with start command."},
    ],
    "deployment": [
        {"type": "accepted_evidence_count", "skill_key": "deployment", "min_depth": "basic", "min": 1, "description": "Deployment configuration or a deploy step is committed."},
    ],
}


def default_criteria(skill_key: str) -> list[dict[str, Any]]:
    crit = CRITERIA_LIBRARY.get(skill_key) or [
        {"type": "accepted_evidence_count", "skill_key": skill_key, "min_depth": "intermediate", "min": 1,
         "description": f"Accepted {skill_name(skill_key)} evidence of at least intermediate depth."},
        {"type": "verdict_at_least", "skill_key": skill_key, "verdict": "DEMONSTRATED", "description": f"{skill_name(skill_key)} verdict becomes DEMONSTRATED."},
    ]
    return [{"id": f"c{i + 1}", **c} for i, c in enumerate(crit)]


class LLMTask(BaseModel):
    title: str = Field(min_length=10, max_length=120)
    objective: str = Field(min_length=20, max_length=600)
    steps: list[str] = Field(min_length=2, max_length=8)
    suggested_files: list[str] = Field(default_factory=list, max_length=8)
    risks: list[str] = Field(default_factory=list, max_length=5)
    effort: str = Field(pattern="^(S|M|L)$")


def _scope_files(db, repository_id: str) -> list[str]:
    sc = db.execute(select(AnalysisScope).where(AnalysisScope.repository_id == repository_id, AnalysisScope.active.is_(True))).scalar_one_or_none()
    if not sc or not sc.last_snapshot_id:
        return []
    return [p for (p,) in db.execute(select(SnapshotFile.path).where(SnapshotFile.snapshot_id == sc.last_snapshot_id))]


async def propose(user_id: str, row_id: str, repository_id: str | None) -> str:
    with session_scope() as db:
        row = db.get(AssessmentRow, row_id)
        if row is None:
            raise AppError("not_found", "Assessment row not found.", status=404)
        a = db.get(Assessment, row.assessment_id)
        if a.user_id != user_id:
            raise AppError("not_found", "Assessment row not found.", status=404)
        req = db.get(Requirement, row.requirement_id)
        jp = db.get(JobPosting, a.job_posting_id)
        repos = [s["repository_id"] for s in a.scopes or []]
        repo_id = repository_id or (repos[0] if repos else None)
        if repo_id is None:
            raise AppError("no_repository", "Select a repository for this task.", status=422)
        repo = db.get(Repository, repo_id)
        files = _scope_files(db, repo_id)
        related = db.execute(select(EvidenceRecord).where(EvidenceRecord.repository_id == repo_id, EvidenceRecord.status == "current",
                                                          EvidenceRecord.verification_status == "accepted",
                                                          EvidenceRecord.skill_key.in_(("api_development", "sql")))).scalars().all()
        anchor_files = sorted({e.path for e in related})[:5]
        ctx = {"skill": skill_name(row.skill_key), "skill_key": row.skill_key, "verdict": row.verdict, "rationale": row.rationale, "quote": req.quote,
               "job": f"{jp.title} at {jp.company}", "source_url": jp.source_url, "repo": repo.full_name, "anchor_files": anchor_files,
               "tests_dir": next((p.split("/")[0] for p in files if p.startswith(("tests/", "test/", "__tests__/"))), None),
               "files_sample": files[:80], "requirement_id": req.id, "job_id": jp.id}
    criteria = default_criteria(ctx["skill_key"])
    title = f"Demonstrate {ctx['skill']} for {ctx['job']}"
    objective = f"Close the '{ctx['skill']}' gap ({ctx['verdict'].replace('_', ' ').lower()}) with real, tested code in {ctx['repo']}."
    steps = [f"Implement {ctx['skill'].lower()} in an existing feature" + (f" (e.g. {', '.join(ctx['anchor_files'][:2])})" if ctx["anchor_files"] else ""),
             "Add or extend tests that exercise the new code", "Push the commit — the coach re-checks the criteria automatically"]
    suggested = ctx["anchor_files"][:3]
    risks: list[str] = []
    effort = "M"
    generator = "template"
    if llm_available(user_id):
        try:
            res = await complete_json(user_id, "Propose ONE small, concrete task that would let this candidate demonstrate the missing skill in their own repository. "
                                               "Suggested files must come from the provided file list or be new files placed next to them. No invented requirements.",
                                      f"Skill: {ctx['skill']}\nVerdict: {ctx['verdict']}\nWhy: {ctx['rationale']}\nJD quote: " + wrap_untrusted("jd", ctx["quote"], 400)
                                      + f"\nRepository: {ctx['repo']}\nExisting relevant files: {ctx['anchor_files']}\nFile list (sample): {ctx['files_sample']}",
                                      LLMTask, max_tokens=1200)
            t: LLMTask = res.data
            title, objective, steps, risks, effort = t.title, t.objective, t.steps, t.risks, t.effort
            suggested = [f for f in t.suggested_files if f in ctx["files_sample"] or "/" in f or "." in f][:6]
            generator = f"llm:{res.model} (criteria are deterministic)"
        except (LLMFailed, NotConfigured):
            pass
    body = "\n".join([
        f"### Why\nThe job posting ({ctx['source_url'] or 'user-supplied'}) requires:\n> {ctx['quote']}\n",
        f"Current assessment: **{ctx['verdict']}** — {ctx['rationale']}\n",
        f"### Objective\n{objective}\n",
        "### Steps\n" + "\n".join(f"- [ ] {s}" for s in steps) + "\n",
        ("### Suggested files\n" + "\n".join(f"- `{f}`" for f in suggested) + "\n") if suggested else "",
        "### Acceptance criteria (checked automatically on every push)\n" + "\n".join(f"- [ ] {c['description']}" + (" *(optional)*" if c.get("optional") else "") for c in criteria),
        "\n### What counts as evidence\nOnly code in the pinned commit that passes the evidence verifier. Recommendations are not achievements.",
        "\n_Generated by AI Career Coach after your confirmation._",
    ])
    with session_scope() as db:
        t = GapTask(user_id=user_id, assessment_row_id=row_id, job_posting_id=ctx["job_id"], requirement_id=ctx["requirement_id"], skill_key=ctx["skill_key"],
                    repository_id=repo_id, title=title[:300], body=body, objective=objective, suggested_files=suggested, acceptance_criteria=criteria,
                    risks=risks, effort_estimate=effort, generator=generator, status="proposed")
        db.add(t)
        db.flush()
        return t.id


async def create_issue(user_id: str, task_id: str, confirm: bool, repository_full_name: str) -> dict[str, Any]:
    if not confirm:
        raise AppError("confirmation_required", "Explicit confirmation is required before creating a GitHub issue.", status=428)
    with session_scope() as db:
        t = db.get(GapTask, task_id)
        if t is None or t.user_id != user_id:
            raise AppError("not_found", "Task not found.", status=404)
        if t.github_issue_url:
            return {"url": t.github_issue_url, "number": t.github_issue_number, "already_created": True}
        repo = db.get(Repository, t.repository_id)
        if repo.full_name != repository_full_name:
            raise AppError("repo_mismatch", "Confirmed repository does not match the task's repository.", status=409)
        token = github_token(db, user_id)
        title, body = t.title, t.body
    if not token:
        raise NotConfigured("GitHub write access", ["GitHub token with Issues: write (fine-grained) or public_repo/repo (classic)"])
    gh = GitHubClient(token)
    try:
        issue = await gh.create_issue(repository_full_name, title, body)
    finally:
        await gh.close()
    with session_scope() as db:
        t = db.get(GapTask, task_id)
        t.github_issue_number, t.github_issue_url, t.status, t.confirmed_at = issue["number"], issue["html_url"], "issue_created", utcnow()
        audit("external_write", {"action": "github_issue_created", "repo": repository_full_name, "issue": issue["html_url"], "task_id": task_id}, user_id, db=db)
    emit("gap_task_updated", {"task_id": task_id, "status": "issue_created", "url": issue["html_url"]}, user_id)
    return {"url": issue["html_url"], "number": issue["number"]}


DEPTH = {"trivial": 0, "basic": 1, "intermediate": 2, "substantial": 3}
VERDICT_ORDER = {"NOT_DEMONSTRATED_IN_SCOPE": 0, "CLAIMED_ONLY": 0, "PARTIAL": 1, "DEMONSTRATED": 2}


def evaluate_criteria(db, t: GapTask, commit_sha: str | None) -> dict[str, Any]:
    ev = db.execute(select(EvidenceRecord).where(EvidenceRecord.repository_id == t.repository_id, EvidenceRecord.status == "current",
                                                 EvidenceRecord.verification_status == "accepted")).scalars().all()
    latest = db.execute(select(Assessment).where(Assessment.job_posting_id == t.job_posting_id).order_by(Assessment.version.desc()).limit(1)).scalar_one_or_none()
    rows = {r.skill_key: r for r in db.execute(select(AssessmentRow).where(AssessmentRow.assessment_id == latest.id)).scalars()} if latest else {}
    files = _scope_files(db, t.repository_id)
    results = []
    for c in t.acceptance_criteria or []:
        typ = c.get("type")
        passed, detail, cites = False, "", []
        if typ == "accepted_evidence_count":
            m = [e for e in ev if e.skill_key == c["skill_key"] and DEPTH.get(e.depth, 0) >= DEPTH.get(c.get("min_depth", "basic"), 1)
                 and (not c.get("source_type") or e.source_type == c["source_type"]) and (not c.get("signal_level") or e.signal_level == c["signal_level"])]
            passed = len(m) >= int(c.get("min", 1))
            detail = f"{len(m)} qualifying evidence item(s), need {c.get('min', 1)}"
            cites = [{"id": e.id, "permalink": e.permalink, "path": e.path} for e in m[:4]]
        elif typ == "verdict_at_least":
            r = rows.get(c["skill_key"])
            passed = bool(r) and VERDICT_ORDER.get(r.verdict, 0) >= VERDICT_ORDER.get(c["verdict"], 2)
            detail = f"current verdict: {r.verdict if r else 'no row'}"
        elif typ == "evidence_detector_present":
            m = [e for e in ev if e.detector == c["detector"]]
            passed = bool(m)
            detail = f"{len(m)} matching item(s)"
            cites = [{"id": e.id, "permalink": e.permalink, "path": e.path} for e in m[:2]]
        elif typ == "path_exists":
            m = [p for p in files if fnmatch.fnmatch(p, c["glob"])]
            passed = bool(m)
            detail = f"{len(m)} matching path(s)"
        results.append({"id": c.get("id"), "description": c.get("description"), "passed": passed, "optional": bool(c.get("optional")),
                        "detail": detail, "citations": cites})
    required = [r for r in results if not r["optional"]]
    return {"commit_sha": commit_sha, "checked_at": utcnow(), "all_passed": bool(required) and all(r["passed"] for r in required), "criteria": results}


async def verify_tasks_for_repo(user_id: str, repository_id: str, commit_sha: str | None, run=None) -> int:
    with session_scope() as db:
        tasks = db.execute(select(GapTask).where(GapTask.user_id == user_id, GapTask.repository_id == repository_id,
                                                 GapTask.status.in_(("proposed", "issue_created", "criteria_met")))).scalars().all()
        auto_close = user_settings(db, user_id).get("auto_close_issues")
        token = github_token(db, user_id)
        to_close = []
        for t in tasks:
            res = evaluate_criteria(db, t, commit_sha)
            t.last_verification = res
            if res["all_passed"] and t.status != "criteria_met":
                t.status = "criteria_met"
                t.verified_commit_sha = commit_sha
                if auto_close and t.github_issue_number:
                    to_close.append((t.id, t.github_issue_number, db.get(Repository, repository_id).full_name, res))
            elif not res["all_passed"] and t.status == "criteria_met":
                t.status = "issue_created" if t.github_issue_url else "proposed"
            emit("gap_task_updated", {"task_id": t.id, "status": t.status, "all_passed": res["all_passed"]}, user_id, db=db)
        n = len(tasks)
    for tid, num, full, res in to_close:
        await close_issue(user_id, tid, True, _auto=True)
    return n


async def close_issue(user_id: str, task_id: str, confirm: bool, _auto: bool = False) -> dict[str, Any]:
    if not confirm:
        raise AppError("confirmation_required", "Confirm closing the GitHub issue.", status=428)
    with session_scope() as db:
        t = db.get(GapTask, task_id)
        if t is None or t.user_id != user_id:
            raise AppError("not_found", "Task not found.", status=404)
        if t.status != "criteria_met" or not (t.last_verification or {}).get("all_passed"):
            raise AppError("criteria_not_met", "Acceptance criteria are not all verified on the latest commit.", status=409)
        repo = db.get(Repository, t.repository_id)
        token = github_token(db, user_id)
        number, ver = t.github_issue_number, t.last_verification
    if number:
        lines = [f"Verified automatically against commit `{(ver.get('commit_sha') or '')[:12]}`:"]
        for c in ver["criteria"]:
            lines.append(f"- {'✅' if c['passed'] else '⬜'} {c['description']} — {c['detail']}" +
                         ("".join(f"\n  - {x['permalink']}" for x in c["citations"][:2]) if c["citations"] else ""))
        gh = GitHubClient(token)
        try:
            await gh.comment_issue(repo.full_name, number, "\n".join(lines))
            await gh.close_issue(repo.full_name, number)
        finally:
            await gh.close()
    with session_scope() as db:
        t = db.get(GapTask, task_id)
        t.status = "closed"
        audit("external_write", {"action": "github_issue_closed", "auto": _auto, "task_id": task_id, "issue": number}, user_id, db=db)
    emit("gap_task_updated", {"task_id": task_id, "status": "closed"}, user_id)
    return {"status": "closed"}
