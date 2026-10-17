"""Background worker: SQLite job queue consumer + GitHub SHA poller.

Run with:  python -m backend.app.workers.worker
"""
from __future__ import annotations

import asyncio
import logging
import os
import socket
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from ..config import get_settings
from ..database import session_scope, utcnow
from ..errors import AppError, ProviderError
from ..integrations.github import GitHubClient
from ..models import AnalysisScope, Repository, SystemState
from ..services import job_queue
from ..services.events import emit
from ..services.integrations_store import github_token
from ..services.migrations import ensure_migrated

log = logging.getLogger("worker")
WORKER_ID = f"{socket.gethostname()}:{os.getpid()}"


async def dispatch(job_type: str, payload: dict) -> dict:
    if job_type == "scan_scope":
        from ..services.reassessment import handle_repo_change

        return await handle_repo_change(payload["scope_id"], payload.get("trigger", "manual_rescan"), payload.get("trigger_ref"),
                                        bool(payload.get("force_full")))
    if job_type == "transcribe_answer":
        from ..services.interviews import transcribe_answer_job

        return await transcribe_answer_job(payload["answer_id"])
    if job_type == "evaluate_answer":
        from ..services.interviews import evaluate_answer_job

        return await evaluate_answer_job(payload["answer_id"])
    if job_type == "sandbox_run":
        from ..services.sandbox import execute_run

        return await execute_run(payload["run_id"])
    if job_type == "reassess_jobs":
        from ..services.reassessment import reassess_all_jobs

        return {"assessments": reassess_all_jobs(payload["user_id"], payload.get("trigger", "profile_update"))}
    raise AppError("unknown_job", f"Unknown job type {job_type}", status=500)


async def run_one() -> bool:
    job = job_queue.claim_next(WORKER_ID)
    if job is None:
        return False
    log.info("claimed %s %s (attempt %s)", job.job_type, job.id, job.attempts)
    emit("job_status", {"job_id": job.id, "job_type": job.job_type, "status": "running", "attempt": job.attempts}, job.user_id)
    try:
        result = await dispatch(job.job_type, job.payload or {})
        job_queue.complete(job.id, result if isinstance(result, dict) else {"result": str(result)})
        emit("job_status", {"job_id": job.id, "job_type": job.job_type, "status": "succeeded"}, job.user_id)
    except ProviderError as e:
        st = job_queue.fail(job.id, e.message, transient=e.transient, retry_after=e.retry_after)
        emit("job_status", {"job_id": job.id, "job_type": job.job_type, "status": st, "error": e.message, "retry_after": e.retry_after}, job.user_id)
    except AppError as e:
        st = job_queue.fail(job.id, f"{e.code}: {e.message}", transient=e.status >= 500 and e.code not in ("unknown_job",))
        emit("job_status", {"job_id": job.id, "job_type": job.job_type, "status": st, "error": e.message}, job.user_id)
    except Exception as e:  # noqa: BLE001
        log.exception("job %s failed", job.id)
        st = job_queue.fail(job.id, f"{type(e).__name__}: {e}", transient=True)
        emit("job_status", {"job_id": job.id, "job_type": job.job_type, "status": st, "error": str(e)[:300]}, job.user_id)
    return True


_poll_backoff_until: dict[str, float] = {}


async def poll_once() -> None:
    s = get_settings()
    now = datetime.now(timezone.utc)
    with session_scope() as db:
        scopes = db.execute(select(AnalysisScope).where(AnalysisScope.active.is_(True), AnalysisScope.last_analyzed_sha.is_not(None))).scalars().all()
        todo = []
        for sc in scopes:
            last = datetime.fromisoformat(sc.last_polled_at) if sc.last_polled_at else None
            if last and now - last < timedelta(seconds=s.poll_interval_seconds):
                continue
            if _poll_backoff_until.get(sc.id, 0) > time.time():
                continue
            repo = db.get(Repository, sc.repository_id)
            todo.append((sc.id, sc.user_id, repo.full_name, sc.branch, sc.last_analyzed_sha, github_token(db, sc.user_id)))
    for scope_id, user_id, full_name, branch, last_sha, token in todo:
        gh = GitHubClient(token)
        try:
            head, not_modified = await gh.branch_head(full_name, branch)
            with session_scope() as db:
                sc = db.get(AnalysisScope, scope_id)
                sc.last_polled_at = utcnow()
                sc.last_seen_head_sha = head
                sc.last_poll_error = None
            if head != last_sha and not job_queue.has_pending("scan_scope", f"scan:{scope_id}:"):
                job_id, created = job_queue.enqueue("scan_scope", {"scope_id": scope_id, "trigger": "poll", "trigger_ref": head},
                                                    f"scan:{scope_id}:{head}", user_id=user_id)
                if created:
                    with session_scope() as db:
                        sc = db.get(AnalysisScope, scope_id)
                        sc.last_event_at, sc.last_event_kind = utcnow(), "poll"
                    emit("change_detected", {"scope_id": scope_id, "repo": full_name, "branch": branch, "head": head, "via": "poll",
                                             "previous": last_sha}, user_id)
        except ProviderError as e:
            _poll_backoff_until[scope_id] = time.time() + (e.retry_after or 300)
            with session_scope() as db:
                sc = db.get(AnalysisScope, scope_id)
                sc.last_polled_at = utcnow()
                sc.last_poll_error = e.message
        except AppError as e:
            _poll_backoff_until[scope_id] = time.time() + 900
            with session_scope() as db:
                sc = db.get(AnalysisScope, scope_id)
                sc.last_polled_at = utcnow()
                sc.last_poll_error = e.message
        finally:
            await gh.close()


def heartbeat(extra: dict | None = None) -> None:
    with session_scope() as db:
        st = db.get(SystemState, "worker_heartbeat")
        val = {"worker_id": WORKER_ID, "at": utcnow(), "poll_interval_seconds": get_settings().poll_interval_seconds, **(extra or {})}
        if st is None:
            db.add(SystemState(key="worker_heartbeat", value=val))
        else:
            st.value = val
            st.updated_at = utcnow()


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    ensure_migrated()
    log.info("worker %s started", WORKER_ID)
    last_recover = 0.0
    last_poll = 0.0
    last_beat = 0.0
    while True:
        try:
            now = time.time()
            if now - last_recover > 60:
                n = job_queue.recover_expired_leases()
                if n:
                    log.warning("recovered %s expired lease(s)", n)
                last_recover = now
            if now - last_beat > 10:
                heartbeat()
                last_beat = now
            if now - last_poll > 15:
                await poll_once()
                last_poll = now
            worked = await run_one()
            if not worked:
                await asyncio.sleep(1.0)
        except Exception:  # noqa: BLE001 - keep the worker alive
            log.exception("worker loop error")
            await asyncio.sleep(3)


if __name__ == "__main__":
    asyncio.run(main())
