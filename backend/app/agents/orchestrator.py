"""Bounded agent runtime: observe → plan → call typed tools → verify → persist → trace.

Every step is recorded as a user-safe action (tool, short input summary, entity ids,
result summary, duration). No chain-of-thought is stored or shown. Runs are bounded by
max tool calls, wall-clock time and LLM calls (configurable in .env).
"""
from __future__ import annotations

import time
from typing import Any, Awaitable, Callable, TypeVar

from ..config import get_settings
from ..database import session_scope, utcnow
from ..errors import AppError
from ..models import AgentAction, AgentRun as AgentRunRow
from ..security import redact
from ..services.events import emit

T = TypeVar("T")

STANDING_GOAL = ("Keep the candidate's skill-gap assessment for the selected real job accurate, current, auditable, "
                 "and grounded in available evidence.")

# Typed tools the agent may call (name -> description). Each is implemented by a service function
# with validated inputs; the registry is exposed at /api/agent/tools for transparency.
TOOL_REGISTRY: dict[str, str] = {
    "fetch_job_posting": "Fetch a public job page (SSRF-safe, robots.txt respected) or a provider API posting.",
    "search_supported_job_sources": "Query configured job providers (Remotive, Arbeitnow, The Muse, RemoteOK, Greenhouse, Lever, Adzuna, SerpApi).",
    "extract_requirements": "Extract candidate requirements (taxonomy matcher + optional LLM) from stored JD text.",
    "validate_requirement_quotes": "Reject any requirement whose quote is not verbatim in the stored JD text.",
    "list_user_repositories": "List repositories the GitHub token can access.",
    "get_repository_tree": "Fetch the recursive tree at a pinned commit SHA.",
    "read_file_at_commit": "Read a file blob at the pinned commit.",
    "search_repository_code": "Search the scanned files of a scope (cached blobs) for a pattern.",
    "get_commit_diff": "Compare two commits and list added/modified/removed/renamed paths.",
    "run_skill_detector": "Run deterministic AST/lexical detectors on a file.",
    "trace_imports_and_callers": "Find references to a symbol across the scanned scope.",
    "record_evidence": "Persist an immutable evidence record with permalink.",
    "verify_evidence": "Adversarial critic: line mapping, generated/vendor, trivial tests, unreferenced code, SQL without execution.",
    "reassess_affected_skills": "Recompute only the gap-matrix rows whose evidence changed; create a new assessment version + diff.",
    "get_public_leetcode_stats": "Fetch public LeetCode stats (best-effort, unofficial endpoint).",
    "parse_user_supplied_profile": "Parse an uploaded resume/LinkedIn PDF export or pasted text into claims.",
    "generate_interview_plan": "Create a versioned, JD-specific interview plan with provenance per question.",
    "generate_question_from_code": "Create a code-defense question from a validated commit/file/line citation.",
    "transcribe_answer": "Transcribe recorded answer audio with the configured STT provider.",
    "evaluate_answer": "Score an answer against its rubric and produce feedback.",
    "preview_gap_issue": "Draft a gap-closing GitHub issue with machine-checkable acceptance criteria.",
    "create_gap_issue_after_confirmation": "Create the GitHub issue only after explicit user confirmation.",
    "verify_gap_acceptance_criteria": "Check acceptance criteria against evidence from the latest real commit.",
    "create_skill_passport": "Issue an Ed25519-signed, revocable evidence passport.",
    "revoke_skill_passport": "Revoke a passport.",
}


class BudgetExceeded(AppError):
    def __init__(self, what: str):
        super().__init__("agent_budget_exceeded", f"Agent run stopped: {what} budget exhausted.", status=503,
                         action="Increase AGENT_MAX_* limits in .env or narrow the repository scope.")


class AgentRun:
    def __init__(self, user_id: str | None, trigger: str, goal: str = STANDING_GOAL):
        s = get_settings()
        self.user_id = user_id
        self.trigger = trigger
        self.started = time.monotonic()
        self.max_calls = s.agent_max_tool_calls
        self.max_seconds = s.agent_max_seconds
        self.max_llm = s.agent_max_llm_calls
        self.calls = 0
        self.llm_calls = 0
        self.seq = 0
        with session_scope() as db:
            row = AgentRunRow(user_id=user_id, trigger=trigger, goal=goal, status="running")
            db.add(row)
            db.flush()
            self.id = row.id
        emit("agent_run_started", {"run_id": self.id, "trigger": trigger}, user_id)

    # ------------------------------------------------------------------ bookkeeping
    def _check_budget(self) -> None:
        if self.calls >= self.max_calls:
            raise BudgetExceeded("tool-call")
        if time.monotonic() - self.started > self.max_seconds:
            raise BudgetExceeded("time")

    def charge_llm(self, provider: str | None = None, model: str | None = None) -> None:
        if self.llm_calls >= self.max_llm:
            raise BudgetExceeded("LLM-call")
        self.llm_calls += 1
        if provider or model:
            with session_scope() as db:
                r = db.get(AgentRunRow, self.id)
                if r:
                    r.model_provider = provider
                    r.model_id = model

    def record(self, action: str, *, tool: str | None = None, input_summary: str = "", result_summary: str = "",
               entity_ids: list[str] | None = None, status: str = "ok", duration_ms: int = 0) -> None:
        self.seq += 1
        payload = {
            "run_id": self.id, "seq": self.seq, "action": action, "tool": tool,
            "input_summary": redact(input_summary)[:500], "result_summary": redact(result_summary)[:800],
            "entity_ids": (entity_ids or [])[:20], "status": status, "duration_ms": duration_ms, "at": utcnow(),
        }
        with session_scope() as db:
            db.add(AgentAction(run_id=self.id, seq=self.seq, action=action, tool=tool, input_summary=payload["input_summary"],
                               result_summary=payload["result_summary"], entity_ids=payload["entity_ids"], status=status,
                               duration_ms=duration_ms))
            r = db.get(AgentRunRow, self.id)
            if r:
                r.step_count = self.seq
            emit("agent_action", payload, self.user_id, db=db)

    async def tool(self, name: str, input_summary: str, fn: Callable[..., Awaitable[T]], *args: Any,
                   summarize: Callable[[T], str] | None = None, entity_ids: Callable[[T], list[str]] | None = None, **kwargs: Any) -> T:
        self._check_budget()
        self.calls += 1
        t0 = time.monotonic()
        try:
            result = await fn(*args, **kwargs)
        except Exception as e:  # noqa: BLE001
            self.record("tool_call", tool=name, input_summary=input_summary, result_summary=f"{type(e).__name__}: {e}",
                        status="error", duration_ms=int((time.monotonic() - t0) * 1000))
            raise
        self.record("tool_call", tool=name, input_summary=input_summary,
                    result_summary=summarize(result) if summarize else "ok",
                    entity_ids=entity_ids(result) if entity_ids else None, duration_ms=int((time.monotonic() - t0) * 1000))
        return result

    def finish(self, status: str = "succeeded", summary: str = "", error: str | None = None) -> None:
        with session_scope() as db:
            r = db.get(AgentRunRow, self.id)
            if r:
                r.status = status
                r.ended_at = utcnow()
                r.summary = redact(summary)[:2000]
                r.error_summary = redact(error)[:2000] if error else None
                r.llm_calls = self.llm_calls
            emit("agent_run_finished", {"run_id": self.id, "status": status, "summary": summary[:300], "error": (error or "")[:300]},
                 self.user_id, db=db)
