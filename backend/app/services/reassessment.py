"""Autonomous reassessment loop triggered by webhook deliveries, SHA polling, scope changes or manual rescans."""
from __future__ import annotations

from typing import Any

from sqlalchemy import select

from ..agents.orchestrator import AgentRun
from ..database import session_scope
from ..models import AnalysisScope, Assessment, JobPosting, Repository
from .assessments import create_assessment
from .events import emit
from .repo_scanner import scan_scope


def jobs_to_reassess(user_id: str) -> list[str]:
    with session_scope() as db:
        tracked = [j for (j,) in db.execute(select(JobPosting.id).where(JobPosting.user_id == user_id, JobPosting.status == "analyzed",
                                                                        JobPosting.is_tracked.is_(True)))]
        assessed = [j for (j,) in db.execute(select(Assessment.job_posting_id).where(Assessment.user_id == user_id).distinct())]
    return list(dict.fromkeys(tracked + assessed))


async def handle_repo_change(scope_id: str, trigger: str, trigger_ref: str | None = None, force_full: bool = False) -> dict[str, Any]:
    with session_scope() as db:
        sc = db.get(AnalysisScope, scope_id)
        if sc is None or not sc.active:
            return {"status": "skipped", "reason": "scope inactive or missing"}
        repo = db.get(Repository, sc.repository_id)
        user_id, full_name, branch = sc.user_id, repo.full_name, sc.branch
    run = AgentRun(user_id, f"{trigger}:{full_name}@{branch}")
    try:
        run.record("observe", input_summary=f"{trigger} event for {full_name}@{branch}" + (f" ({trigger_ref})" if trigger_ref else ""),
                   result_summary="reconciling against the actual branch head on GitHub (event order is not trusted)")
        result = await scan_scope(scope_id, trigger, run, trigger_ref=trigger_ref, force_full=force_full)
        if result.noop:
            run.finish("succeeded", f"{full_name}@{branch} already up to date at {(result.commit_sha or '')[:12]}")
            return {"status": "noop", "commit_sha": result.commit_sha}
        initial = result.previous_sha is None or result.mode == "full"
        affected = None if initial else result.affected_skills
        run.record("plan", result_summary=(f"re-evaluate all requirements (full scan of {result.commit_sha[:12]})" if affected is None else
                                           f"re-evaluate only rows for: {', '.join(sorted(affected)) or 'no skills (no evidence changed)'}; carry the rest unchanged"))
        created: list[str] = []
        for job_id in jobs_to_reassess(user_id):
            aid = create_assessment(job_id, trigger if not initial or trigger in ("webhook", "poll") else "initial", trigger_ref=trigger_ref,
                                    trigger_commit=result.commit_sha, affected_skills=affected, changed_paths=result.changed_paths,
                                    commit_html_url=result.commit_html_url, agent_run_id=run.id)
            if aid:
                created.append(aid)
                run.record("tool_call", tool="reassess_affected_skills", input_summary=f"job {job_id[:8]}",
                           result_summary=f"new assessment version persisted ({aid[:8]})", entity_ids=[aid])
        try:
            from .gap_tasks import verify_tasks_for_repo

            verified = await verify_tasks_for_repo(user_id, result.repository_id, result.commit_sha, run)
            if verified:
                run.record("tool_call", tool="verify_gap_acceptance_criteria", input_summary=f"{verified} open task(s)",
                           result_summary="acceptance criteria checked against the new commit")
        except Exception as e:  # noqa: BLE001
            run.record("verify", result_summary=f"gap-task verification error: {type(e).__name__}: {str(e)[:200]}", status="error")
        summary = (f"{full_name}@{result.commit_sha[:12]} ({result.mode}): +{result.evidence_added}/-{result.evidence_superseded} evidence, "
                   f"{result.evidence_kept} unchanged; {len(created)} assessment(s) updated")
        run.finish("succeeded", summary)
        emit("reassessment_completed", {"scope_id": scope_id, "commit_sha": result.commit_sha, "assessments": created,
                                        "affected_skills": sorted(result.affected_skills), "mode": result.mode}, user_id)
        return {"status": "ok", "commit_sha": result.commit_sha, "assessments": created, "mode": result.mode}
    except Exception as e:
        run.finish("failed", error=getattr(e, "message", None) or f"{type(e).__name__}: {e}")
        raise


def reassess_all_jobs(user_id: str, trigger: str) -> list[str]:
    run = AgentRun(user_id, trigger)
    out = []
    for job_id in jobs_to_reassess(user_id):
        aid = create_assessment(job_id, trigger, agent_run_id=run.id)
        if aid:
            out.append(aid)
            run.record("tool_call", tool="reassess_affected_skills", input_summary=f"job {job_id[:8]} ({trigger})", result_summary=f"assessment {aid[:8]}")
    run.finish("succeeded", f"{len(out)} assessment(s) recomputed after {trigger}")
    return out
