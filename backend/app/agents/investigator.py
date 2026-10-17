"""Bounded LLM investigator for ambiguous evidence (e.g. SQL text with no visible execution path,
or an implementation function that looks unreferenced).

The model may call three read-only tools over the scanned scope (search_repository_code,
read_file_at_commit, trace_imports_and_callers) for at most MAX_STEPS steps, then conclude.
A "supported" conclusion must cite a path:line which is re-checked deterministically; the model
can never directly create or upgrade evidence.
"""
from __future__ import annotations

import json
import re
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import select

from ..database import session_scope
from ..errors import AppError
from ..integrations.github import permalink
from ..integrations.llm import complete_json, wrap_untrusted
from ..models import EvidenceRecord
from ..services.repo_scanner import read_cached_file, search_scope_code
from .orchestrator import AgentRun

MAX_STEPS = 6
EXEC_RX = re.compile(r"\.(execute|executemany|executescript|query|raw|fetch|fetchrow|fetchval|exec_driver_sql|run|all|get)\s*\(|\bsql\s*`", re.I)


class Step(BaseModel):
    action: Literal["search_repository_code", "read_file_at_commit", "trace_imports_and_callers", "conclude"]
    pattern: str | None = Field(default=None, max_length=200)
    path: str | None = Field(default=None, max_length=400)
    start_line: int | None = Field(default=None, ge=1)
    end_line: int | None = Field(default=None, ge=1)
    symbol: str | None = Field(default=None, max_length=120)
    conclusion: Literal["supported", "still_ambiguous", "not_supported"] | None = None
    cited_path: str | None = None
    cited_line: int | None = None
    explanation: str | None = Field(default=None, max_length=600)


SYSTEM = ("You are verifying one ambiguous piece of portfolio evidence. Use the read-only tools to find whether the code is actually executed "
          "or referenced (e.g. the SQL string is passed to an execute call, or the function is called/registered elsewhere). Reply with one JSON "
          "action per turn. When done, use action=conclude with conclusion supported/still_ambiguous/not_supported; for 'supported' you MUST cite "
          "cited_path and cited_line of the line that executes/references it. Never guess line numbers.")


def _symbol_names(e: EvidenceRecord) -> list[str]:
    names: list[str] = []
    snippet = e.snippet or ""
    m = re.search(r"^\s*(\w+)\s*=\s*[\"'fbr]", snippet, re.M)
    if m:
        names.append(m.group(1))
    m = re.search(r"\b(?:def|function|class)\s+(\w+)", snippet)
    if m:
        names.append(m.group(1))
    return names


def _check_citation(scope_id: str, e: EvidenceRecord, path: str, line: int) -> tuple[bool, str]:
    text = read_cached_file(scope_id, path)
    if text is None:
        return False, "cited file is not in the analysed snapshot"
    lines = text.split("\n")
    if line < 1 or line > len(lines):
        return False, "cited line does not exist"
    src = lines[line - 1]
    if src.strip().startswith(("#", "//", "*")):
        return False, "cited line is a comment"
    names = _symbol_names(e)
    if path == e.path and e.start_line <= line <= e.end_line and not EXEC_RX.search(src):
        return False, "cited line is the evidence itself, not a use of it"
    if e.skill_key == "sql":
        if EXEC_RX.search(src) and (any(n in src for n in names) or path == e.path and e.start_line <= line <= e.end_line):
            return True, f"execution call at {path}:{line}"
        return False, "cited line does not execute the SQL"
    if names and any(re.search(r"(?<![\w$])" + re.escape(n) + r"(?![\w$])", src) for n in names) and not re.match(r"\s*(def|function|class)\s", src):
        return True, f"reference at {path}:{line}"
    return False, "cited line does not reference the symbol"


async def investigate(user_id: str, evidence_id: str) -> dict:
    with session_scope() as db:
        e = db.get(EvidenceRecord, evidence_id)
        if e is None or e.user_id != user_id:
            raise AppError("not_found", "Evidence not found.", status=404)
        if e.verification_status != "ambiguous" or e.status != "current":
            raise AppError("not_ambiguous", "Only current ambiguous evidence can be investigated.", status=409)
        db.expunge(e)
    run = AgentRun(user_id, f"investigate_evidence:{evidence_id[:8]}", goal="Resolve whether ambiguous evidence is actually executed/referenced.")
    history: list[str] = [f"EVIDENCE {e.repo_full_name}:{e.path} L{e.start_line}-{e.end_line} skill={e.skill_key}: {e.reason}",
                          "NOTES: " + "; ".join(e.verification_notes or []), wrap_untrusted(e.path, e.snippet, 3000)]
    outcome = {"conclusion": "still_ambiguous", "explanation": "investigation did not reach a verified conclusion", "verified": False}
    try:
        for i in range(MAX_STEPS):
            run.charge_llm()
            res = await complete_json(user_id, SYSTEM, "\n\n".join(history) + f"\n\nStep {i + 1}/{MAX_STEPS}. Next action?", Step, max_tokens=600)
            st: Step = res.data
            if st.action == "conclude":
                outcome = {"conclusion": st.conclusion or "still_ambiguous", "explanation": st.explanation or "", "verified": False,
                           "cited": f"{st.cited_path}:{st.cited_line}" if st.cited_path else None}
                break
            if st.action == "search_repository_code":
                hits = search_scope_code(e.scope_id, st.pattern or "", limit=15)
                obs = json.dumps(hits)[:3000]
                run.record("tool_call", tool="search_repository_code", input_summary=st.pattern or "", result_summary=f"{len(hits)} hit(s)")
            elif st.action == "read_file_at_commit":
                text = read_cached_file(e.scope_id, st.path or "") or ""
                ls = text.split("\n")
                a, b = max(1, st.start_line or 1), min(len(ls), st.end_line or (st.start_line or 1) + 40)
                obs = "\n".join(f"{n}: {ls[n - 1]}" for n in range(a, b + 1))[:4000] if text else "(file not in snapshot)"
                run.record("tool_call", tool="read_file_at_commit", input_summary=f"{st.path}:{a}-{b}", result_summary=f"{max(0, b - a + 1)} line(s)")
            else:
                sym = st.symbol or ""
                hits = search_scope_code(e.scope_id, r"(?<![\w$])" + re.escape(sym) + r"(?![\w$])", limit=20) if sym else []
                obs = json.dumps(hits)[:3000]
                run.record("tool_call", tool="trace_imports_and_callers", input_summary=sym, result_summary=f"{len(hits)} reference(s)")
            history.append(f"ACTION {st.model_dump_json(exclude_none=True)}\nRESULT:\n{wrap_untrusted('tool', obs, 4000)}")
        if outcome["conclusion"] == "supported" and outcome.get("cited"):
            path, _, ln = outcome["cited"].rpartition(":")
            ok, why = _check_citation(e.scope_id, e, path, int(ln) if ln.isdigit() else -1)
            outcome["verified"] = ok
            outcome["check"] = why
            run.record("verify", input_summary=outcome["cited"], result_summary=("citation verified: " if ok else "citation rejected: ") + why,
                       status="ok" if ok else "rejected")
        with session_scope() as db:
            old = db.get(EvidenceRecord, evidence_id)
            if outcome["conclusion"] == "supported" and outcome["verified"]:
                old.status = "historical"
                old.superseded_reason = f"resolved by agent investigation: {outcome['check']}"
                new = EvidenceRecord(**{c.name: getattr(old, c.name) for c in EvidenceRecord.__table__.columns if c.name not in ("id", "status", "superseded_reason", "created_at")})
                new.status = "current"
                new.verification_status = "accepted"
                new.reachability = "referenced"
                new.confidence = round(min(0.85, old.confidence + 0.25), 2)
                new.verification_notes = (old.verification_notes or []) + [f"Agent investigation found {outcome['check']} (deterministically re-checked)."]
                new.permalink = permalink(old.repo_full_name, old.commit_sha, old.path, old.start_line, old.end_line)
                db.add(new)
                skill = old.skill_key
            else:
                notes = list(old.verification_notes or [])
                notes.append(f"Agent investigation: {outcome['conclusion']} — {outcome['explanation'][:200]}" + (f" (citation rejected: {outcome.get('check')})" if outcome.get("check") and not outcome["verified"] else ""))
                old.verification_notes = notes
                skill = None
        if skill:
            from ..services.reassessment import jobs_to_reassess
            from ..services.assessments import create_assessment

            for jid in jobs_to_reassess(user_id):
                create_assessment(jid, "agent_investigation", trigger_ref=evidence_id, affected_skills={skill}, agent_run_id=run.id)
        run.finish("succeeded", f"investigation: {outcome['conclusion']} (verified={outcome['verified']})")
        return outcome
    except Exception as ex:
        run.finish("failed", error=getattr(ex, "message", str(ex)))
        raise
