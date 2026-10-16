"""Evidence-backed gap matrix: verdicts, versioned assessments and diffs.

Verdict semantics (Phase 1, never mixed with interview/LeetCode/runtime signals):
- DEMONSTRATED: accepted evidence meeting the documented detector criteria
- PARTIAL: some evidence, but depth/completeness insufficient (incl. ambiguous or dependency-only)
- NOT_DEMONSTRATED_IN_SCOPE: nothing adequate found in the exact declared scope
- CLAIMED_ONLY: a supplied resume/profile claims it, the scope does not substantiate it
  (only produced when a profile/resume was actually supplied)
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..analyzers.findings import DEPTH_ORDER
from ..analyzers.taxonomy import get_skill, skill_name
from ..database import session_scope, utcnow
from ..integrations.github import commit_url
from ..models import (AnalysisScope, Assessment, AssessmentDiff, AssessmentRow, AssessmentRowEvidence, EvidenceFreshnessEvent,
                      EvidenceRecord, JobPosting, LeetCodeSnapshot, ProfileClaim, ProfileDocument, Repository, Requirement, SandboxRun,
                      ScopeSnapshot, UnderstandingEvidence)
from .events import emit

PRIORITY_WEIGHT = {"must_have": 3, "unclear": 2, "preferred": 1}
VERDICT_CREDIT = {"DEMONSTRATED": 1.0, "PARTIAL": 0.5, "CLAIMED_ONLY": 0.0, "NOT_DEMONSTRATED_IN_SCOPE": 0.0}
CONFIG_SKILLS = {"ci_cd", "containerization", "deployment", "kubernetes", "terraform", "aws", "gcp", "azure", "cloud"}
LANG_OR_LIB_PREFIX = ("language:", "library:")

RECOMMENDATIONS: dict[str, str] = {
    "api_development": "Add a route with request validation, error responses (4xx/5xx) and real handler logic, and reference it from tests.",
    "sql": "Add parameterised SQL queries or ORM queries that are executed from a request handler or service, ideally inside a transaction.",
    "automated_testing": "Add a test module with at least two tests that call your code and assert on real results (not `assert True`), and run them in CI.",
    "ci_cd": "Add a GitHub Actions workflow that installs dependencies and runs your test suite on every push.",
    "containerization": "Add a Dockerfile with a pinned base image, dependency install, non-root USER and a CMD/ENTRYPOINT.",
    "deployment": "Deploy the service (e.g. Render/Fly/Vercel) and commit the platform config or a deploy workflow step plus a health endpoint.",
    "auth_security": "Implement authentication (hashed passwords or OAuth/JWT verification) and protect at least one route.",
    "caching": "Add a cache (Redis or in-process with expiry) in front of an expensive read path and test invalidation.",
    "concurrency": "Use asyncio tasks/thread pools (or goroutines/Promise.all) for independent I/O and handle cancellation/errors.",
    "observability": "Add structured logging with request IDs and an error tracker or metrics endpoint.",
}


def _claims_by_skill(db: Session, user_id: str) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = defaultdict(list)
    rows = db.execute(
        select(ProfileClaim, ProfileDocument).join(ProfileDocument, ProfileClaim.document_id == ProfileDocument.id)
        .where(ProfileDocument.user_id == user_id, ProfileDocument.active.is_(True), ProfileClaim.skill_key.is_not(None))
    ).all()
    for c, d in rows:
        if len(out[c.skill_key]) < 3:
            out[c.skill_key].append({"claim_id": c.id, "text": c.text[:200], "evidence_class": c.evidence_class,
                                     "document": d.filename or d.kind, "page": c.page})
    return out


def _leetcode_signal(db: Session, user_id: str, skill_key: str) -> dict[str, Any] | None:
    sk = get_skill(skill_key)
    if not sk or not sk.leetcode_tags:
        return None
    snap = db.execute(select(LeetCodeSnapshot).where(LeetCodeSnapshot.user_id == user_id, LeetCodeSnapshot.status.in_(("ok", "partial", "user_supplied")))
                      .order_by(LeetCodeSnapshot.retrieved_at.desc()).limit(1)).scalar_one_or_none()
    if not snap:
        return None
    tags = (snap.fields or {}).get("tag_counts") or {}
    relevant = {t: n for t, n in tags.items() if t in sk.leetcode_tags}
    solved = (snap.fields or {}).get("solved") or {}
    return {"type": "PROBLEM_SOLVING_SIGNAL", "provider": snap.provider, "username": snap.username, "retrieved_at": snap.retrieved_at,
            "solved_total": solved.get("All"), "relevant_tags": relevant, "snapshot_id": snap.id,
            "note": "LeetCode activity is a problem-solving signal only; it never proves implementation skills."}


def _understanding_signal(db: Session, user_id: str, skill_key: str) -> dict[str, Any] | None:
    u = db.execute(select(UnderstandingEvidence).where(UnderstandingEvidence.user_id == user_id, UnderstandingEvidence.skill_key == skill_key)
                   .order_by(UnderstandingEvidence.created_at.desc()).limit(1)).scalar_one_or_none()
    if not u:
        return None
    return {"type": u.evidence_type, "label": u.label, "rubric_score": u.rubric_score, "session_id": u.session_id, "at": u.created_at,
            "note": "Interview understanding is recorded separately and never changes the repository verdict."}


def _runtime_signal(db: Session, user_id: str, repo_ids: list[str]) -> dict[str, Any] | None:
    r = db.execute(select(SandboxRun).where(SandboxRun.user_id == user_id, SandboxRun.repository_id.in_(repo_ids), SandboxRun.status == "completed")
                   .order_by(SandboxRun.created_at.desc()).limit(1)).scalar_one_or_none()
    if not r:
        return None
    return {"type": "RUNTIME_EXECUTION", "run_id": r.id, "commit_sha": r.commit_sha, "command": r.command, "exit_code": r.exit_code,
            "passed": r.passed, "failed": r.failed, "skipped": r.skipped, "at": r.finished_at,
            "note": "Result of executing this commit in an isolated sandbox; not a universal proof of correctness."}


def compute_row(skill_key: str, evidence: list[EvidenceRecord], claims: list[dict], scopes_info: list[dict]) -> dict[str, Any]:
    sk = get_skill(skill_key)
    name = skill_name(skill_key)
    accepted = [e for e in evidence if e.verification_status == "accepted"]
    ambiguous = [e for e in evidence if e.verification_status == "ambiguous"]
    rejected = [e for e in evidence if e.verification_status == "rejected"]
    non_dep = [e for e in accepted if e.source_type != "DEPENDENCY_DECLARATION"]
    deps = [e for e in accepted if e.source_type == "DEPENDENCY_DECLARATION"]
    strong = [e for e in non_dep if DEPTH_ORDER.get(e.depth, 0) >= 2 and e.signal_level in ("meaningful_implementation", "tests_found")
              and not (skill_key == "automated_testing" and e.source_type == "CODE_CONFIG")]
    detector = sk.detector if sk else None
    trace = {
        "repositories": scopes_info,
        "detector": detector,
        "detector_supported": bool(detector),
        "accepted": len(accepted), "ambiguous": len(ambiguous), "rejected": len(rejected),
        "rejected_examples": [{"path": e.path, "lines": f"{e.start_line}-{e.end_line}", "why": (e.verification_notes or [e.reason])[0][:200],
                               "permalink": e.permalink} for e in rejected[:5]],
        "ambiguous_examples": [{"path": e.path, "lines": f"{e.start_line}-{e.end_line}", "why": "; ".join(e.verification_notes or [e.reason])[:240],
                                "permalink": e.permalink} for e in ambiguous[:5]],
    }
    files_scanned = sum(s.get("files_scanned", 0) for s in scopes_info)
    scope_desc = ", ".join(f"{s['repo']}@{s['branch']} ({s['commit_sha'][:7]}, {s['files_scanned']} files)" for s in scopes_info) or "no repositories selected"
    signal_level = None
    cited: list[EvidenceRecord] = []
    verdict = "NOT_DEMONSTRATED_IN_SCOPE"

    if not detector:
        rationale = (f"{name} cannot be assessed from repository code by the available detectors; it was not demonstrated in this analysed scope "
                     f"({scope_desc}).")
        conf, note = 0.3, "No automated detector exists for this skill, so absence of evidence says very little."
    elif not scopes_info:
        rationale = "No repository scope has been analysed yet."
        conf, note = 0.0, "Connect GitHub and select repositories to assess this requirement."
    else:
        if skill_key == "automated_testing":
            demonstrated = len(strong) >= 2 or any(e.depth == "substantial" for e in strong)
        elif (detector or "").startswith(LANG_OR_LIB_PREFIX):
            usage = [e for e in non_dep if DEPTH_ORDER.get(e.depth, 0) >= 1]
            demonstrated = any(DEPTH_ORDER.get(e.depth, 0) >= 2 for e in usage) and len({e.path for e in usage}) >= 1 and \
                (any(e.depth == "substantial" for e in usage) or len({e.path for e in usage}) >= 2 or any(e.signal_level == "meaningful_implementation" for e in usage))
            demonstrated = demonstrated or len({e.path for e in usage if DEPTH_ORDER.get(e.depth, 0) >= 1}) >= 3
            strong = sorted(usage, key=lambda e: (-DEPTH_ORDER.get(e.depth, 0), -e.confidence))
        elif skill_key in CONFIG_SKILLS:
            cfg = [e for e in non_dep if DEPTH_ORDER.get(e.depth, 0) >= 2]
            demonstrated = bool(cfg)
            strong = cfg or strong
        else:
            demonstrated = len(strong) >= 1
        if demonstrated:
            verdict = "DEMONSTRATED"
            cited = sorted(strong, key=lambda e: (-DEPTH_ORDER.get(e.depth, 0), -e.confidence))[:6]
            signal_level = "tests_found" if skill_key == "automated_testing" else max((e.signal_level for e in cited), key=lambda s: ["dependency_detected", "config_present", "code_usage", "tests_found", "meaningful_implementation"].index(s))
            locs = "; ".join(f"{e.repo_full_name}:{e.path} L{e.start_line}-{e.end_line}" for e in cited[:3])
            rationale = f"{len(cited)} accepted evidence item(s) meet the {name} criteria, e.g. {cited[0].reason} ({locs})."
            conf = round(max(e.confidence for e in cited), 2)
            note = "Static analysis of the pinned commit; it shows the code exists and how it is wired, not that it runs in production."
        elif non_dep or ambiguous or deps:
            verdict = "PARTIAL"
            cited = sorted(non_dep + ambiguous, key=lambda e: (-DEPTH_ORDER.get(e.depth, 0), -e.confidence))[:6] or deps[:3]
            if skill_key == "automated_testing" and len(strong) == 1:
                why = "only one meaningful test was found"
            elif non_dep:
                why = f"evidence is {max((e.depth for e in non_dep), key=lambda d: DEPTH_ORDER.get(d, 0))} depth ({cited[0].reason})"
            elif ambiguous:
                why = f"candidate evidence is ambiguous: {'; '.join(ambiguous[0].verification_notes or [ambiguous[0].reason])[:200]}"
            else:
                why = f"only a dependency declaration was found ({deps[0].reason}); no code usage observed"
            signal_level = "dependency_detected" if not non_dep and not ambiguous else (cited[0].signal_level if cited else None)
            rationale = f"Partially demonstrated: {why}."
            conf = round(min(0.7, max([e.confidence for e in cited] or [0.3])), 2)
            note = "Evidence exists but does not meet the documented threshold for this skill."
        else:
            searched = f"searched {files_scanned} file(s) in {scope_desc} with the {detector} detector"
            rationale = f"Not demonstrated in this analysed scope: {searched}; no accepted evidence." + \
                (f" {len(rejected)} candidate(s) were rejected by the critic." if rejected else "")
            conf = 0.6 if files_scanned >= 10 else 0.4
            note = "Missing evidence is not lack of ability — the skill may live in private or unselected repositories."
        if verdict == "NOT_DEMONSTRATED_IN_SCOPE" and claims:
            verdict = "CLAIMED_ONLY"
            rationale = f"Your supplied profile/resume claims {name} (“{claims[0]['text'][:90]}”), but the analysed scope does not substantiate it. " + rationale
    if not detector and claims:
        verdict = "CLAIMED_ONLY"
        rationale = f"Claimed in your supplied profile (“{claims[0]['text'][:90]}”); this skill cannot be verified from repository code by the available detectors."
    rec = None
    if verdict != "DEMONSTRATED":
        rec = "RECOMMENDED: " + RECOMMENDATIONS.get(skill_key, f"Build a small, tested feature that uses {name} in one of your selected repositories and push it.")
    return {"verdict": verdict, "rationale": rationale, "confidence": conf, "confidence_note": note, "signal_level": signal_level,
            "evidence_ids": [e.id for e in cited], "search_trace": trace, "recommendation": rec}


def _scopes_info(db: Session, user_id: str) -> tuple[list[dict], list[str], list[str]]:
    scopes = db.execute(select(AnalysisScope).where(AnalysisScope.user_id == user_id, AnalysisScope.active.is_(True),
                                                    AnalysisScope.last_snapshot_id.is_not(None))).scalars().all()
    info = []
    for s in scopes:
        snap = db.get(ScopeSnapshot, s.last_snapshot_id)
        repo = db.get(Repository, s.repository_id)
        info.append({"scope_id": s.id, "scope_version": s.version, "repository_id": repo.id, "repo": repo.full_name, "branch": s.branch,
                     "commit_sha": snap.commit_sha, "snapshot_id": snap.id, "files_scanned": snap.scanned_file_count,
                     "include": s.include_patterns, "exclude": s.exclude_patterns, "analyzed_at": snap.created_at})
    return info, [s.id for s in scopes], [s.repository_id for s in scopes]


def create_assessment(job_posting_id: str, trigger_type: str, *, trigger_ref: str | None = None, trigger_commit: str | None = None,
                      affected_skills: set[str] | None = None, changed_paths: list[dict] | None = None, commit_html_url: str | None = None,
                      agent_run_id: str | None = None) -> str | None:
    """Create a new immutable assessment version. Rows whose skill was not affected are carried over unchanged."""
    with session_scope() as db:
        posting = db.get(JobPosting, job_posting_id)
        if posting is None:
            return None
        user_id = posting.user_id
        reqs = db.execute(select(Requirement).where(Requirement.posting_id == job_posting_id)).scalars().all()
        if not reqs:
            return None
        scopes_info, scope_ids, repo_ids = _scopes_info(db, user_id)
        prev = db.execute(select(Assessment).where(Assessment.job_posting_id == job_posting_id).order_by(Assessment.version.desc()).limit(1)).scalar_one_or_none()
        prev_rows: dict[str, AssessmentRow] = {}
        if prev is not None:
            for r in db.execute(select(AssessmentRow).where(AssessmentRow.assessment_id == prev.id)).scalars():
                prev_rows[r.requirement_id] = r
        prev_scope_keys = {(s["scope_id"]) for s in (prev.scopes or [])} if prev else set()
        scope_changed = prev is None or prev_scope_keys != {s["scope_id"] for s in scopes_info}
        claims = _claims_by_skill(db, user_id)
        evidence_by_skill: dict[str, list[EvidenceRecord]] = defaultdict(list)
        if scope_ids:
            for e in db.execute(select(EvidenceRecord).where(EvidenceRecord.scope_id.in_(scope_ids), EvidenceRecord.status == "current")).scalars():
                evidence_by_skill[e.skill_key].append(e)
        version = (prev.version + 1) if prev else 1
        a = Assessment(user_id=user_id, job_posting_id=job_posting_id, version=version, trigger_type=trigger_type, trigger_ref=trigger_ref,
                       trigger_commit_sha=trigger_commit, scopes=scopes_info, agent_run_id=agent_run_id)
        db.add(a)
        db.flush()
        deltas: list[dict[str, Any]] = []
        counts: dict[str, int] = defaultdict(int)
        weighted, total_w = 0.0, 0.0
        recomputed = 0
        recompute_all = scope_changed or affected_skills is None or trigger_type in ("profile_update", "manual_rescan", "requirements_changed")
        for req in reqs:
            old = prev_rows.get(req.id)
            must_recompute = recompute_all or old is None or req.skill_key in (affected_skills or set())
            if not must_recompute and old is not None:
                row = AssessmentRow(assessment_id=a.id, requirement_id=req.id, skill_key=req.skill_key, verdict=old.verdict, rationale=old.rationale,
                                    confidence=old.confidence, confidence_note=old.confidence_note, signal_level=old.signal_level,
                                    search_trace=old.search_trace, signals=old.signals, recommendation=old.recommendation,
                                    evaluated_at=old.evaluated_at, carried_from_row_id=old.id, changed=False)
                db.add(row)
                db.flush()
                for link in db.execute(select(AssessmentRowEvidence).where(AssessmentRowEvidence.row_id == old.id)).scalars():
                    db.add(AssessmentRowEvidence(row_id=row.id, evidence_id=link.evidence_id))
            else:
                recomputed += 1
                computed = compute_row(req.skill_key, evidence_by_skill.get(req.skill_key, []), claims.get(req.skill_key, []), scopes_info)
                signals = {"claims": claims.get(req.skill_key, []), "leetcode": _leetcode_signal(db, user_id, req.skill_key),
                           "understanding": _understanding_signal(db, user_id, req.skill_key),
                           "runtime": _runtime_signal(db, user_id, repo_ids) if req.skill_key == "automated_testing" and repo_ids else None}
                row = AssessmentRow(assessment_id=a.id, requirement_id=req.id, skill_key=req.skill_key, verdict=computed["verdict"],
                                    rationale=computed["rationale"], confidence=computed["confidence"], confidence_note=computed["confidence_note"],
                                    signal_level=computed["signal_level"], search_trace=computed["search_trace"], signals=signals,
                                    recommendation=computed["recommendation"], evaluated_at=utcnow(), changed=True)
                db.add(row)
                db.flush()
                for eid in computed["evidence_ids"]:
                    db.add(AssessmentRowEvidence(row_id=row.id, evidence_id=eid))
                old_ev = set()
                if old is not None:
                    old_ev = {l.evidence_id for l in db.execute(select(AssessmentRowEvidence).where(AssessmentRowEvidence.row_id == old.id)).scalars()}
                if old is None or old.verdict != row.verdict or old_ev != set(computed["evidence_ids"]):
                    deltas.append({"requirement_id": req.id, "skill_key": req.skill_key, "skill": req.display_name, "priority": req.priority,
                                   "from_verdict": old.verdict if old else None, "to_verdict": row.verdict,
                                   "direction": _direction(old.verdict if old else None, row.verdict),
                                   "added_evidence": sorted(set(computed["evidence_ids"]) - old_ev),
                                   "removed_evidence": sorted(old_ev - set(computed["evidence_ids"])), "rationale": row.rationale})
            counts[row.verdict] += 1
            if not req.out_of_scope:
                w = PRIORITY_WEIGHT.get(req.priority, 2)
                total_w += w
                weighted += w * VERDICT_CREDIT.get(row.verdict, 0)
        coverage = round(100 * weighted / total_w, 1) if total_w else 0.0
        a.summary = {
            "counts": dict(counts), "requirements": len(reqs), "coverage_indicator": coverage,
            "coverage_formula": "Σ(priority_weight × verdict_credit) / Σ(priority_weight) × 100; weights must_have=3, unclear=2, preferred=1; "
                                "credit DEMONSTRATED=1, PARTIAL=0.5, others=0; requirements marked out-of-scope excluded.",
            "disclaimer": "Requirement coverage indicator only — not a hiring probability or prediction.",
            "recomputed_rows": recomputed,
            "carried_rows": len(reqs) - recomputed,
            "affected_skills": sorted(affected_skills) if affected_skills is not None else "all",
        }
        if prev is not None:
            db.add(AssessmentDiff(from_assessment_id=prev.id, to_assessment_id=a.id, trigger_type=trigger_type, trigger_ref=trigger_ref,
                                  commit_sha=trigger_commit, commit_url=commit_html_url, changed_paths=(changed_paths or [])[:300],
                                  affected_skills=sorted(affected_skills or []), row_deltas=deltas))
        for r_ in db.execute(select(AssessmentRow).where(AssessmentRow.assessment_id == a.id, AssessmentRow.verdict == "CLAIMED_ONLY")).scalars():
            exists = db.execute(select(EvidenceFreshnessEvent.id).where(EvidenceFreshnessEvent.user_id == user_id, EvidenceFreshnessEvent.kind == "contradiction",
                                                                        EvidenceFreshnessEvent.source_id == r_.requirement_id, EvidenceFreshnessEvent.status == "open")).first()
            if not exists:
                db.add(EvidenceFreshnessEvent(user_id=user_id, kind="contradiction", source_type="requirement", source_id=r_.requirement_id,
                                              reason=f"Profile claims {skill_name(r_.skill_key)} but the analysed repositories do not show it — needs review "
                                                     f"(the repository may be private or not selected).", trigger_ref=a.id, detail={"assessment_id": a.id}))
        emit("assessment_created", {"assessment_id": a.id, "job_posting_id": job_posting_id, "version": version, "trigger_type": trigger_type,
                                    "commit_sha": trigger_commit, "changes": len(deltas), "coverage": coverage}, user_id, db=db)
        return a.id


def _direction(old: str | None, new: str) -> str:
    order = {"NOT_DEMONSTRATED_IN_SCOPE": 0, "CLAIMED_ONLY": 0, "PARTIAL": 1, "DEMONSTRATED": 2}
    if old is None:
        return "new"
    if order[new] > order[old]:
        return "improved"
    if order[new] < order[old]:
        return "regressed"
    return "evidence_changed"


def tracked_job_ids(user_id: str) -> list[str]:
    with session_scope() as db:
        return [j for (j,) in db.execute(select(JobPosting.id).where(JobPosting.user_id == user_id, JobPosting.is_tracked.is_(True),
                                                                     JobPosting.status == "analyzed"))]


def serialize_assessment(db: Session, a: Assessment) -> dict[str, Any]:
    rows = db.execute(select(AssessmentRow, Requirement).join(Requirement, AssessmentRow.requirement_id == Requirement.id)
                      .where(AssessmentRow.assessment_id == a.id)).all()
    ev_links = db.execute(select(AssessmentRowEvidence.row_id, EvidenceRecord).join(EvidenceRecord, AssessmentRowEvidence.evidence_id == EvidenceRecord.id)
                          .where(AssessmentRowEvidence.row_id.in_([r.id for r, _ in rows]))).all() if rows else []
    by_row: dict[str, list[dict]] = defaultdict(list)
    for row_id, e in ev_links:
        by_row[row_id].append(evidence_dict(e))
    prio = {"must_have": 0, "unclear": 1, "preferred": 2}
    out_rows = []
    for r, req in sorted(rows, key=lambda x: (prio.get(x[1].priority, 3), x[1].display_name)):
        out_rows.append({
            "id": r.id, "requirement": requirement_dict(req), "skill_key": r.skill_key, "verdict": r.verdict, "rationale": r.rationale,
            "confidence": r.confidence, "confidence_note": r.confidence_note, "signal_level": r.signal_level, "search_trace": r.search_trace,
            "signals": r.signals, "recommendation": r.recommendation, "evaluated_at": r.evaluated_at, "changed": r.changed,
            "carried_from_row_id": r.carried_from_row_id, "evidence": by_row.get(r.id, []),
        })
    return {"id": a.id, "job_posting_id": a.job_posting_id, "version": a.version, "trigger_type": a.trigger_type, "trigger_ref": a.trigger_ref,
            "trigger_commit_sha": a.trigger_commit_sha, "trigger_commit_url": commit_url(a.scopes[0]["repo"], a.trigger_commit_sha) if a.trigger_commit_sha and a.scopes else None,
            "scopes": a.scopes, "summary": a.summary, "created_at": a.created_at, "agent_run_id": a.agent_run_id, "rows": out_rows}


def requirement_dict(r: Requirement) -> dict[str, Any]:
    return {"id": r.id, "skill_key": r.skill_key, "display_name": r.display_name, "original_term": r.original_term, "category": r.category,
            "priority": r.priority, "quote": r.quote, "char_start": r.char_start, "char_end": r.char_end, "source_url": r.source_url,
            "extraction_method": r.extraction_method, "extraction_version": r.extraction_version, "confidence": r.confidence,
            "detector_supported": r.detector_supported, "out_of_scope": r.out_of_scope}


def evidence_dict(e: EvidenceRecord, with_snippet: bool = True) -> dict[str, Any]:
    d = {"id": e.id, "skill_key": e.skill_key, "source_type": e.source_type, "repo": e.repo_full_name, "branch": e.branch, "commit_sha": e.commit_sha,
         "path": e.path, "start_line": e.start_line, "end_line": e.end_line, "detector": e.detector, "detector_version": e.detector_version,
         "node_type": e.node_type, "signal_level": e.signal_level, "depth": e.depth, "file_kind": e.file_kind, "reachability": e.reachability,
         "verification_status": e.verification_status, "verification_notes": e.verification_notes, "llm_corroboration": e.llm_corroboration,
         "confidence": e.confidence, "reason": e.reason, "permalink": e.permalink, "status": e.status, "superseded_at_sha": e.superseded_at_sha,
         "superseded_reason": e.superseded_reason, "snippet_digest": e.snippet_digest, "created_at": e.created_at, "scope_id": e.scope_id}
    if with_snippet:
        d["snippet"] = e.snippet
    return d


def latest_assessment_id(db: Session, job_posting_id: str) -> str | None:
    a = db.execute(select(Assessment.id).where(Assessment.job_posting_id == job_posting_id).order_by(Assessment.version.desc()).limit(1)).scalar_one_or_none()
    return a


def assessment_count(db: Session, job_posting_id: str) -> int:
    return db.execute(select(func.count()).select_from(Assessment).where(Assessment.job_posting_id == job_posting_id)).scalar_one()
