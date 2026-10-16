"""Durable SQLite-backed background job queue (no Redis).

- Idempotent enqueue via unique idempotency_key.
- Claims happen inside BEGIN IMMEDIATE so two workers never claim the same job.
- Leases expire; jobs whose worker crashed are re-queued by recover_expired_leases().
- Exponential backoff with jitter; terminal failures become status='dead'.
- Never hold a transaction open while running the job body.
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import or_, select

from ..database import immediate_transaction, new_id, session_scope, utcnow
from ..models import BackgroundJob

LEASE_SECONDS = 600


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def enqueue(
    job_type: str,
    payload: dict[str, Any],
    idempotency_key: str,
    user_id: str | None = None,
    max_attempts: int = 5,
    delay_seconds: int = 0,
) -> tuple[str, bool]:
    """Insert a job unless one with the same idempotency key exists. Returns (job_id, created)."""
    with immediate_transaction() as db:
        existing = db.execute(select(BackgroundJob).where(BackgroundJob.idempotency_key == idempotency_key)).scalar_one_or_none()
        if existing is not None:
            return existing.id, False
        job = BackgroundJob(
            id=new_id(),
            job_type=job_type,
            payload=payload,
            idempotency_key=idempotency_key,
            user_id=user_id,
            max_attempts=max_attempts,
            next_run_at=_iso(_now() + timedelta(seconds=delay_seconds)),
        )
        db.add(job)
        return job.id, True


def claim_next(worker_id: str, job_types: list[str] | None = None) -> BackgroundJob | None:
    now = _iso(_now())
    with immediate_transaction() as db:
        q = select(BackgroundJob).where(
            BackgroundJob.status == "queued", BackgroundJob.next_run_at <= now
        )
        if job_types:
            q = q.where(BackgroundJob.job_type.in_(job_types))
        job = db.execute(q.order_by(BackgroundJob.next_run_at).limit(1)).scalar_one_or_none()
        if job is None:
            return None
        job.status = "running"
        job.attempts += 1
        job.claimed_by = worker_id
        job.lease_expires_at = _iso(_now() + timedelta(seconds=LEASE_SECONDS))
        job.updated_at = utcnow()
        db.flush()
        db.expunge(job)
        return job


def complete(job_id: str, result: dict[str, Any] | None = None) -> None:
    with immediate_transaction() as db:
        job = db.get(BackgroundJob, job_id)
        if job is None:
            return
        job.status = "succeeded"
        job.result = result or {}
        job.lease_expires_at = None
        job.error_summary = None


def fail(job_id: str, error: str, transient: bool = True, retry_after: int | None = None) -> str:
    """Record a failure. Returns the new status (queued for retry, or dead)."""
    with immediate_transaction() as db:
        job = db.get(BackgroundJob, job_id)
        if job is None:
            return "missing"
        job.error_summary = error[:2000]
        job.lease_expires_at = None
        if transient and job.attempts < job.max_attempts:
            backoff = retry_after if retry_after else min(900, (2 ** job.attempts) * 5)
            backoff = backoff + random.uniform(0, backoff * 0.25)
            job.status = "queued"
            job.next_run_at = _iso(_now() + timedelta(seconds=backoff))
        else:
            job.status = "dead"
        return job.status


def recover_expired_leases() -> int:
    now = _iso(_now())
    with immediate_transaction() as db:
        jobs = db.execute(
            select(BackgroundJob).where(
                BackgroundJob.status == "running",
                BackgroundJob.lease_expires_at.is_not(None),
                BackgroundJob.lease_expires_at < now,
            )
        ).scalars().all()
        for j in jobs:
            j.status = "queued" if j.attempts < j.max_attempts else "dead"
            j.error_summary = (j.error_summary or "") + " | lease expired (worker crash or timeout); re-queued"
            j.lease_expires_at = None
            j.next_run_at = now
        return len(jobs)


def has_pending(job_type: str, key_prefix: str) -> bool:
    with session_scope() as db:
        row = db.execute(
            select(BackgroundJob.id).where(
                BackgroundJob.job_type == job_type,
                BackgroundJob.idempotency_key.like(key_prefix + "%"),
                or_(BackgroundJob.status == "queued", BackgroundJob.status == "running"),
            ).limit(1)
        ).first()
        return row is not None


def queue_stats() -> dict[str, int]:
    with session_scope() as db:
        rows = db.execute(select(BackgroundJob.status)).scalars().all()
    out: dict[str, int] = {}
    for s in rows:
        out[s] = out.get(s, 0) + 1
    return out


def recent_jobs(limit: int = 30, user_id: str | None = None) -> list[dict[str, Any]]:
    with session_scope() as db:
        q = select(BackgroundJob).order_by(BackgroundJob.created_at.desc()).limit(limit)
        if user_id:
            q = q.where(or_(BackgroundJob.user_id == user_id, BackgroundJob.user_id.is_(None)))
        jobs = db.execute(q).scalars().all()
        return [
            {
                "id": j.id,
                "job_type": j.job_type,
                "status": j.status,
                "attempts": j.attempts,
                "max_attempts": j.max_attempts,
                "next_run_at": j.next_run_at,
                "error_summary": j.error_summary,
                "created_at": j.created_at,
                "updated_at": j.updated_at,
            }
            for j in jobs
        ]


def retry_dead(job_id: str) -> bool:
    with immediate_transaction() as db:
        job = db.get(BackgroundJob, job_id)
        if job is None or job.status not in ("dead", "failed"):
            return False
        job.status = "queued"
        job.attempts = 0
        job.next_run_at = utcnow()
        return True


__all__ = [
    "enqueue",
    "claim_next",
    "complete",
    "fail",
    "recover_expired_leases",
    "has_pending",
    "queue_stats",
    "recent_jobs",
    "retry_dead",
]
