"""Webhook security/idempotency, durable SQLite queue, migrations and integrity constraints."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from backend.app.database import db_health, get_engine, session_scope
from backend.app.models import BackgroundJob, JobPosting, Requirement, WebhookDelivery
from backend.app.security import compute_github_signature, verify_github_signature
from backend.app.services import job_queue
from backend.tests.helpers import make_scope

SECRET = "test-webhook-secret"


@pytest.fixture
def client():
    from backend.app.main import app

    with TestClient(app) as c:
        yield c


def _push(repo_id="42", ref="refs/heads/main", after="a" * 40):
    return json.dumps({"ref": ref, "after": after, "repository": {"id": int(repo_id), "full_name": "student/payments-api"}, "commits": [{}]}).encode()


def _post(client, body, delivery="d-1", sig=None, event="push"):
    return client.post("/api/github/webhook", content=body, headers={
        "X-Hub-Signature-256": sig if sig is not None else compute_github_signature(SECRET, body),
        "X-GitHub-Delivery": delivery, "X-GitHub-Event": event, "Content-Type": "application/json"})


def test_signature_helpers_constant_time():
    body = b'{"a":1}'
    sig = compute_github_signature(SECRET, body)
    assert verify_github_signature(SECRET, body, sig)
    assert not verify_github_signature(SECRET, body + b" ", sig)
    assert not verify_github_signature(SECRET, body, None)
    assert not verify_github_signature(SECRET, body, "sha1=abc")


def test_invalid_signature_rejected_and_no_job(client):
    make_scope()
    r = _post(client, _push(), sig="sha256=" + "0" * 64)
    assert r.status_code == 401
    with session_scope() as db:
        assert db.execute(select(BackgroundJob)).first() is None
        d = db.execute(select(WebhookDelivery)).scalar_one()
        assert d.signature_status == "invalid" and d.status == "rejected"


def test_valid_push_enqueues_once_and_duplicates_are_idempotent(client):
    make_scope()
    body = _push()
    r1 = _post(client, body, delivery="dup-1")
    assert r1.status_code == 202 and r1.json()["status"] == "queued"
    r2 = _post(client, body, delivery="dup-1")
    assert r2.status_code == 200 and r2.json()["status"] == "duplicate"
    r3 = _post(client, body, delivery="dup-2")  # different delivery, same commit -> same idempotency key
    with session_scope() as db:
        jobs = db.execute(select(BackgroundJob)).scalars().all()
        assert len(jobs) == 1 and jobs[0].job_type == "scan_scope"
        assert len(db.execute(select(WebhookDelivery)).scalars().all()) == 2
    assert r3.status_code == 202


def test_push_for_unselected_branch_or_repo_is_ignored(client):
    make_scope()
    assert _post(client, _push(ref="refs/heads/feature"), delivery="x1").json()["status"] == "ignored"
    assert _post(client, _push(repo_id="999"), delivery="x2").json()["status"] == "ignored"
    assert _post(client, b"{}", delivery="x3", event="ping").json()["status"] == "pong"


def test_queue_claim_retry_dead_and_lease_recovery():
    jid, created = job_queue.enqueue("t", {}, "k1", max_attempts=2)
    assert created and job_queue.enqueue("t", {}, "k1")[1] is False
    j = job_queue.claim_next("w1")
    assert j.id == jid and job_queue.claim_next("w2") is None  # exclusive claim
    assert job_queue.fail(jid, "boom", transient=True) == "queued"
    with session_scope() as db:
        db.get(BackgroundJob, jid).next_run_at = datetime.now(timezone.utc).isoformat()
    job_queue.claim_next("w1")
    assert job_queue.fail(jid, "boom again", transient=True) == "dead"  # max attempts reached
    jid2, _ = job_queue.enqueue("t", {}, "k2")
    job_queue.claim_next("w1")
    with session_scope() as db:
        db.get(BackgroundJob, jid2).lease_expires_at = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
    assert job_queue.recover_expired_leases() == 1
    with session_scope() as db:
        assert db.get(BackgroundJob, jid2).status == "queued"


def test_migrations_pragmas_and_constraints(fresh_db):
    h = db_health()
    assert h["foreign_keys"] is True and h["journal_mode"] == "wal" and h["migration_version"] == "0002_accounts"
    with session_scope() as db:
        db.add(WebhookDelivery(delivery_id="same", signature_status="valid", payload_digest="x"))
    with pytest.raises(IntegrityError):
        with session_scope() as db:
            db.add(WebhookDelivery(delivery_id="same", signature_status="valid", payload_digest="y"))
    with pytest.raises(IntegrityError):  # FK enforced
        with session_scope() as db:
            db.add(Requirement(posting_id="missing", skill_key="sql", display_name="SQL", original_term="SQL", category="database", priority="must_have",
                               quote="SQL", char_start=0, char_end=3, extraction_method="t", extraction_version="t"))


def test_cascade_delete_removes_children():
    from backend.app.services.integrations_store import current_user_id

    uid = current_user_id()
    with session_scope() as db:
        jp = JobPosting(user_id=uid, ingestion_method="user_paste", raw_text="x" * 50, content_hash="h")
        db.add(jp)
        db.flush()
        db.add(Requirement(posting_id=jp.id, skill_key="sql", display_name="SQL", original_term="SQL", category="database", priority="must_have",
                           quote="SQL", char_start=0, char_end=3, extraction_method="t", extraction_version="t"))
        jid = jp.id
    with session_scope() as db:
        db.delete(db.get(JobPosting, jid))
    with get_engine().connect() as c:
        assert c.execute(text("select count(*) from requirements")).scalar() == 0


@pytest.mark.parametrize("url", ["http://127.0.0.1:8000/x", "http://localhost/", "http://10.0.0.5/", "http://169.254.169.254/latest", "file:///etc/passwd", "ftp://example.com"])
def test_ssrf_protection_blocks_private_and_non_http(url):
    import asyncio

    from backend.app.errors import AppError
    from backend.app.integrations.safe_fetch import validate_public_url

    with pytest.raises(AppError):
        asyncio.run(validate_public_url(url))
