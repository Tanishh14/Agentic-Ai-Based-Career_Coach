"""Requirement extraction/quote validation, LLM schema validation + fallback, verdict semantics, passport signatures."""
from __future__ import annotations

import asyncio

import pytest

from backend.app.agents.jd_extractor import LLMExtraction, LLMRequirement, deterministic_extract, extract, merge_llm, validate_quote
from backend.app.analyzers.taxonomy import find_skill_mentions, normalize_term
from backend.app.services.assessments import compute_row

JD = "About us\nWe build payments.\n\nRequirements:\n- Strong Python and REST APIs\n- SQL databases (PostgreSQL)\n\nNice to have:\n- Docker experience is a plus\n"


def test_quotes_are_verbatim_with_offsets():
    for r in deterministic_extract(JD):
        assert JD[r.char_start:r.char_end] == r.quote
    keys = {r.skill_key: r for r in deterministic_extract(JD)}
    assert keys["sql"].priority == "must_have" and keys["containerization"].priority == "preferred"


def test_whitespace_normalized_quote_validation_and_rejection():
    assert validate_quote(JD, "Strong   Python and\nREST APIs") is not None
    assert validate_quote(JD, "Kubernetes experience required") is None


def test_llm_invented_quotes_are_rejected_and_valid_ones_kept():
    llm = LLMExtraction(requirements=[
        LLMRequirement(term="Kubernetes", quote="Must know Kubernetes", priority="must_have"),
        LLMRequirement(term="PostgreSQL", quote="SQL databases (PostgreSQL)", priority="must_have"),
        LLMRequirement(term="Rust", quote="Strong Python and REST APIs", priority="must_have"),
    ])
    merged, rejected = merge_llm(JD, deterministic_extract(JD), llm)
    assert {r["term"] for r in rejected} == {"Kubernetes", "Rust"}
    assert "kubernetes" not in {r.skill_key for r in merged}


def test_sparse_posting_warns_and_invents_nothing():
    res = asyncio.run(extract("u", "Software intern. Python required. Apply now to join our team today please."))
    assert len(res.requirements) <= 2 and any("Sparse posting" in w for w in res.warnings)


def test_synonym_normalisation():
    assert normalize_term("PostgreSQL")[0] == "sql"
    assert normalize_term("Spring Boot")[0] == "spring"
    assert [h[0].key for h in find_skill_mentions("SQL and NoSQL databases")] == ["sql", "nosql"]
    assert find_skill_mentions("Let's go to market") == []


def test_llm_invalid_json_retries_then_falls_back(monkeypatch):
    from backend.app.integrations import llm
    from backend.app.services.integrations_store import LLMProviderConfig

    calls = []

    async def fake_chat(p, messages, json_mode, temperature, max_tokens):
        calls.append(p.name)
        if p.name == "llm_primary":
            return "not json at all"
        return '{"requirements": [], "company": null, "title": null}'

    monkeypatch.setattr(llm, "_chat", fake_chat)
    monkeypatch.setattr(llm, "providers_for", lambda uid: [LLMProviderConfig("llm_primary", "http://a", "", "m1", "env"),
                                                          LLMProviderConfig("llm_fallback", "http://b", "", "m2", "env")])
    res = asyncio.run(llm.complete_json("u", "sys", "user", LLMExtraction))
    assert res.provider == "llm_fallback" and calls.count("llm_primary") == 3


def test_llm_all_providers_fail_raises_and_deterministic_data_retained(monkeypatch):
    from backend.app.integrations import llm
    from backend.app.services.integrations_store import LLMProviderConfig

    async def boom(*a, **k):
        raise llm._Transient("rate limited", retry_after=0)

    real_sleep = asyncio.sleep

    async def no_wait(_s):
        await real_sleep(0)

    monkeypatch.setattr(llm, "_chat", boom)
    monkeypatch.setattr(llm.asyncio, "sleep", no_wait)
    monkeypatch.setattr(llm, "providers_for", lambda uid: [LLMProviderConfig("llm_primary", "http://a", "", "m1", "env")])
    res = asyncio.run(extract("u", JD))
    assert res.requirements and any("LLM extraction failed" in w for w in res.warnings)


class _E:
    def __init__(self, **kw):
        self.__dict__.update(dict(id="e", verification_status="accepted", source_type="CODE_IMPLEMENTATION", depth="intermediate",
                                  signal_level="meaningful_implementation", confidence=0.8, path="a.py", start_line=1, end_line=5,
                                  repo_full_name="s/r", reason="r", verification_notes=[], permalink="p"))
        self.__dict__.update(kw)


SCOPE = [{"scope_id": "s", "repo": "s/r", "branch": "main", "commit_sha": "a" * 40, "files_scanned": 20}]


def test_verdict_semantics():
    assert compute_row("sql", [_E()], [], SCOPE)["verdict"] == "DEMONSTRATED"
    assert compute_row("sql", [_E(source_type="DEPENDENCY_DECLARATION", depth="trivial", signal_level="dependency_detected")], [], SCOPE)["verdict"] == "PARTIAL"
    assert compute_row("sql", [], [], SCOPE)["verdict"] == "NOT_DEMONSTRATED_IN_SCOPE"
    assert compute_row("sql", [], [{"text": "SQL expert", "claim_id": "c", "evidence_class": "CLAIMED_RESUME"}], SCOPE)["verdict"] == "CLAIMED_ONLY"
    one_test = _E(source_type="CODE_TEST", signal_level="tests_found")
    assert compute_row("automated_testing", [one_test], [], SCOPE)["verdict"] == "PARTIAL"
    assert compute_row("automated_testing", [one_test, _E(id="e2", source_type="CODE_TEST", signal_level="tests_found")], [], SCOPE)["verdict"] == "DEMONSTRATED"
    r = compute_row("communication", [], [], SCOPE)
    assert r["verdict"] == "NOT_DEMONSTRATED_IN_SCOPE" and not r["search_trace"]["detector_supported"]
    assert compute_row("sql", [_E(verification_status="ambiguous")], [], SCOPE)["verdict"] == "PARTIAL"
    assert compute_row("sql", [], [], SCOPE)["recommendation"].startswith("RECOMMENDED")


def test_passport_sign_verify_tamper_revoke(fake_repo):
    from backend.app.database import session_scope
    from backend.app.models import Assessment, AssessmentRow
    from backend.app.services import passport
    from backend.app.services.integrations_store import current_user_id
    from backend.tests.helpers import make_job, make_scope, scan
    from backend.tests.test_judge_twists import API_ONLY

    fake_repo.commit({"main.py": API_ONLY}, "a")
    job = make_job()
    scan(make_scope(), "initial")
    with session_scope() as db:
        a = db.query(Assessment).filter(Assessment.job_posting_id == job).order_by(Assessment.version.desc()).first()
        rows = [r.id for r in db.query(AssessmentRow).filter(AssessmentRow.assessment_id == a.id, AssessmentRow.verdict == "DEMONSTRATED")]
        aid = a.id
    uid = current_user_id()
    p = passport.create_passport(uid, aid, rows, title="t", visibility="public", include_permalinks=True, include_display_name=False, expires_days=30)
    v = passport.verify(p["artifact_id"])
    assert v["valid"] and v["signature_valid"]
    with session_scope() as db:
        from backend.app.models import SkillPassport

        doc = db.query(SkillPassport).filter(SkillPassport.artifact_id == p["artifact_id"]).one()
        payload, sig = dict(doc.payload), doc.signature
    payload["skills"] = [dict(s, verdict="DEMONSTRATED") for s in payload["skills"]] + [{"skill": "fake"}]
    assert not passport.verify(p["artifact_id"], payload, sig)["signature_valid"]
    passport.revoke(uid, p["artifact_id"], "test")
    v2 = passport.verify(p["artifact_id"])
    assert v2["revoked"] and not v2["valid"]
