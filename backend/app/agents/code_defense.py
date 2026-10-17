"""Code-Defense questions from the candidate's own analysed code, plus optional bug-diagnosis challenges.

Before a question is created the cited snippet is re-read from GitHub at the pinned commit and
its digest compared with the stored evidence, so questions never reference code the agent has
not inspected. Bug challenges are in-memory hypothetical diffs — repository code is never modified.
"""
from __future__ import annotations

import difflib
from typing import Any, Literal

from pydantic import BaseModel, Field

from ..analyzers.taxonomy import skill_name
from ..database import session_scope
from ..errors import AppError
from ..integrations.github import GitHubClient
from ..models import EvidenceRecord, Repository
from ..security import sha256_hex
from ..services.integrations_store import github_token
from .interview_architect import rubric_for

FOCUS_BY_NODE = {
    "sql_execute_call": "transaction boundaries, parameterisation and what happens when the query fails",
    "orm_query": "query efficiency (N+1), transaction scope and error handling",
    "decorated_route_handler": "input validation, error handling and the status codes returned",
    "route_registration": "input validation, error handling and the status codes returned",
    "test_function": "what behaviour the test proves, what it does not cover and how it isolates dependencies",
    "test_case": "what behaviour the test proves, what it does not cover and how it isolates dependencies",
    "auth_call": "the security properties, token/password lifecycle and possible attacks",
    "concurrency_primitive": "race conditions, cancellation and failure handling",
    "cache_client": "invalidation, staleness and failure of the cache",
    "dockerfile": "image size, security (user, base image) and reproducibility",
    "workflow": "what the pipeline guarantees and what could still break in production",
}


async def validate_citation(user_id: str, evidence_id: str) -> dict[str, Any]:
    with session_scope() as db:
        e = db.get(EvidenceRecord, evidence_id)
        if e is None or e.user_id != user_id:
            raise AppError("not_found", "Evidence not found.", status=404)
        if e.verification_status == "rejected":
            raise AppError("evidence_rejected", "Rejected evidence cannot be used for code defense.", status=422)
        token = github_token(db, user_id)
        d = {"evidence_id": e.id, "repo": e.repo_full_name, "path": e.path, "commit_sha": e.commit_sha, "start_line": e.start_line,
             "end_line": e.end_line, "permalink": e.permalink, "skill_key": e.skill_key, "node_type": e.node_type, "reason": e.reason,
             "stored_digest": e.snippet_digest}
    gh = GitHubClient(token)
    try:
        raw = await gh.file_at_commit(d["repo"], d["path"], d["commit_sha"])
    finally:
        await gh.close()
    if raw is None:
        raise AppError("citation_invalid", "The cited file does not exist at the cited commit.", status=422)
    lines = raw.decode("utf-8", "replace").split("\n")
    snippet = "\n".join(lines[d["start_line"] - 1 : d["end_line"]])
    if sha256_hex(snippet) != d["stored_digest"]:
        raise AppError("citation_mismatch", "The cited lines at this commit no longer match the stored evidence digest.", status=409)
    d["snippet"] = snippet
    d["validated"] = True
    return d


def template_question(cc: dict[str, Any]) -> tuple[str, list[str]]:
    focus = FOCUS_BY_NODE.get(cc.get("node_type") or "", "the design decisions, trade-offs and failure modes")
    prompt = (f"Look at {cc['path']} lines {cc['start_line']}-{cc['end_line']} in {cc['repo']} (commit {cc['commit_sha'][:7]}). "
              f"Explain what this code does and why you wrote it this way, focusing on {focus}. What would you change for production?")
    return prompt, rubric_for(cc.get("skill_key"), "project_deep_dive")["key_points"]


class CodeQuestion(BaseModel):
    prompt: str = Field(min_length=40, max_length=800)
    key_points: list[str] = Field(min_length=2, max_length=6)
    focus: Literal["intent", "tradeoffs", "failure_modes", "security", "performance", "testing", "improvement"]


class BugChallenge(BaseModel):
    mutated_snippet: str = Field(min_length=10, max_length=6000)
    symptom: str = Field(min_length=10, max_length=600)
    bug_description: str = Field(min_length=10, max_length=600)
    key_points: list[str] = Field(min_length=2, max_length=5)


async def llm_question(user_id: str, cc: dict[str, Any]) -> tuple[CodeQuestion, str, str]:
    from ..integrations.llm import complete_json, wrap_untrusted

    system = ("You are interviewing a candidate about code THEY wrote. Ask ONE probing question about the exact snippet shown: intent, "
              "trade-offs, failure modes, security, performance or testing. Refer only to what is visible in the snippet; do not invent "
              "functions or line-level details that are not shown.")
    user = (f"Skill: {skill_name(cc['skill_key'])}. Detector finding: {cc['reason']}\nFile {cc['repo']}:{cc['path']} "
            f"L{cc['start_line']}-{cc['end_line']} @ {cc['commit_sha'][:12]}\n" + wrap_untrusted(cc["path"], cc["snippet"], 4000))
    res = await complete_json(user_id, system, user, CodeQuestion, max_tokens=800)
    return res.data, res.provider, res.model


async def llm_bug_challenge(user_id: str, cc: dict[str, Any]) -> tuple[dict[str, Any], str]:
    from ..integrations.llm import complete_json, wrap_untrusted

    system = ("Create a realistic HYPOTHETICAL bug-diagnosis exercise from this real function: return a copy of the snippet with ONE subtle, "
              "plausible bug introduced (change at most 4 lines; keep everything else identical), the observable symptom a user would "
              "report, the intended bug description (for the grader only), and key points of a good diagnosis.")
    res = await complete_json(user_id, system, wrap_untrusted(cc["path"], cc["snippet"], 4000), BugChallenge, max_tokens=2500)
    ch: BugChallenge = res.data
    orig = cc["snippet"].split("\n")
    mut = ch.mutated_snippet.rstrip("\n").split("\n")
    changed = sum(1 for op in difflib.ndiff(orig, mut) if op.startswith(("+ ", "- ")))
    if changed == 0 or changed > 10:
        raise AppError("challenge_invalid", "The generated mutation was empty or too large; try again.", status=502)
    diff = "\n".join(difflib.unified_diff(orig, mut, fromfile=f"a/{cc['path']} (your commit)", tofile=f"b/{cc['path']} (hypothetical)", lineterm=""))
    return {"diff": diff, "symptom": ch.symptom, "bug_description": ch.bug_description, "key_points": ch.key_points,
            "label": "Hypothetical bug — generated in memory, never committed, not executed.", "generator": f"{res.provider}:{res.model}"}, res.model
