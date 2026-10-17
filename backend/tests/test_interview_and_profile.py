"""Interview plan/state machine/evaluation (no LLM configured → template + heuristic paths), consent, claims."""
from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select

from backend.app.errors import AppError
from backend.app.models import InterviewQuestion, UnderstandingEvidence
from backend.app.services import interviews as svc
from backend.app.services.integrations_store import current_user_id
from backend.tests.helpers import latest, make_job, make_scope, scan
from backend.tests.test_judge_twists import API_ONLY, DB_CODE, MAIN_USING_DB, TESTS


def _setup(fake_repo):
    fake_repo.commit({"main.py": MAIN_USING_DB, "db.py": DB_CODE, "tests/test_main.py": TESTS}, "base")
    job = make_job()
    scan(make_scope(), "initial")
    return job


def test_plan_is_grounded_in_real_requirements_and_evidence(fake_repo):
    job = _setup(fake_repo)
    sid = asyncio.run(svc.create_session(current_user_id(), job, 7))
    from backend.app.database import session_scope

    with session_scope() as db:
        qs = db.execute(select(InterviewQuestion).where(InterviewQuestion.session_id == sid)).scalars().all()
        assert len(qs) >= 4
        kinds = {q.provenance["kind"] for q in qs}
        assert "requirement" in kinds or "gap_row" in kinds
        assert any(q.evidence_id for q in qs)  # project deep dive on real cited code
        for q in qs:
            assert q.rubric["key_points"] and q.prep_seconds > 0 and q.answer_seconds > 0
        assert len({q.prompt for q in qs}) == len(qs)


def test_typed_answer_flow_and_understanding_evidence_separate_from_code_verdict(fake_repo):
    job = _setup(fake_repo)
    uid = current_user_id()
    _, before = latest(job)
    sid = asyncio.run(svc.create_session(uid, job, 4))
    with pytest.raises(AppError):  # must start first
        svc.submit_answer(uid, sid, "x", typed_text="a", audio=None, audio_mime=None, duration_seconds=10, timed_out=False, skipped=False)
    svc.start_session(uid, sid, {"camera": False, "microphone": False, "audio_upload": False})
    from backend.app.database import session_scope

    with session_scope() as db:
        q = svc.ordered_questions(db, sid)[0]
        qid = q.id
    with pytest.raises(AppError):  # audio without upload consent
        svc.submit_answer(uid, sid, qid, typed_text=None, audio=b"x" * 1000, audio_mime="audio/webm", duration_seconds=10, timed_out=False, skipped=False)
    aid = svc.submit_answer(uid, sid, qid, typed_text="I validate input with pydantic, return 422 on errors, use parameterized SQL in a transaction and test edge cases.",
                            audio=None, audio_mime=None, duration_seconds=40, timed_out=False, skipped=False)
    svc.edit_transcript(uid, aid, "I validate input with pydantic models and return 422/404 status codes; SQL is parameterized; I test failure paths with pytest.")
    svc.request_evaluation(uid, aid)
    out = asyncio.run(svc.evaluate_answer_job(aid))
    assert out["status"] == "evaluated" and out["overall"] > 0
    with session_scope() as db:
        u = db.execute(select(UnderstandingEvidence)).scalars().all()
        assert u and u[0].evidence_type == "INTERVIEW_EVIDENCE"
    _, after = latest(job)
    assert {k: r.verdict for k, r in after.items()} == {k: r.verdict for k, r in before.items()}
    rep = svc.complete(uid, sid)
    assert rep["answered"] == 1 and "not a hiring prediction" in rep["overall_formula"]


def test_timeout_with_no_answer_is_recorded_not_invented(fake_repo):
    job = _setup(fake_repo)
    uid = current_user_id()
    sid = asyncio.run(svc.create_session(uid, job, 3))
    svc.start_session(uid, sid, {})
    from backend.app.database import session_scope
    from backend.app.models import InterviewAnswer

    with session_scope() as db:
        qid = svc.ordered_questions(db, sid)[0].id
    aid = svc.submit_answer(uid, sid, qid, typed_text="", audio=None, audio_mime=None, duration_seconds=150, timed_out=True, skipped=False)
    with session_scope() as db:
        a = db.get(InterviewAnswer, aid)
        assert a.timed_out and a.transcript_final == "" and a.error


def test_profile_claims_are_spans_and_claimed_only_verdict(fake_repo):
    from backend.app.services.documents import ExtractedDoc
    from backend.app.services.profile import claim_audit, ingest_profile
    from backend.app.services.reassessment import reassess_all_jobs

    fake_repo.commit({"main.py": API_ONLY}, "api only")
    job = make_job()
    scan(make_scope(), "initial")
    uid = current_user_id()
    text = "SKILLS\nPython, SQL, Docker, Kubernetes\nEXPERIENCE\nBuilt payment APIs"
    asyncio.run(ingest_profile(uid, "resume", ExtractedDoc(text, [0], "text"), "cv.txt", "text/plain", len(text)))
    claims = claim_audit(uid)
    sql = [c for c in claims if c["skill_key"] == "sql"][0]
    assert sql["status"] == "not demonstrated in current GitHub scope" and text[sql["span"][0]:sql["span"][1]].strip() in text
    reassess_all_jobs(uid, "profile_update")
    _, rows = latest(job)
    assert rows["sql"].verdict == "CLAIMED_ONLY"
    assert rows["api_development"].verdict == "DEMONSTRATED"
