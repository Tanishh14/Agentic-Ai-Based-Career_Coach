"""JD-specific interview plan generation.

Inputs are the selected real posting, its validated requirements, the current gap matrix,
cited repository evidence, supplied profile claims and LeetCode signal. Every question
carries provenance (requirement / evidence / gap row / claim) and a rubric.

With an LLM configured, the model drafts the plan and every question is validated
(provenance ids must exist, no duplicates, no vague prompts). Without an LLM, questions are
generated deterministically from the same real data (labelled "template generator").
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from ..analyzers.taxonomy import skill_name

QTYPES = ["technical_fundamentals", "project_deep_dive", "system_scenario", "problem_solving", "testing_security", "behavioral",
          "debugging", "gap_probe"]
DIMENSIONS = ["technical_correctness", "depth", "structure", "relevance", "communication"]

# Expert key points used for deterministic rubrics (domain knowledge, not candidate data).
KEY_POINTS: dict[str, list[tuple[str, list[str]]]] = {
    "api_development": [("Resource modelling & HTTP semantics (methods, status codes)", ["status code", "get", "post", "put", "idempot", "rest", "resource"]),
                        ("Input validation & error handling", ["validat", "schema", "error", "400", "422", "exception"]),
                        ("Auth & rate limiting at the edge", ["auth", "token", "rate limit", "throttl"]),
                        ("Versioning / backward compatibility", ["version", "backward", "compatib", "deprecat"]),
                        ("Pagination & performance", ["paginat", "cursor", "limit", "cache", "latency"])],
    "sql": [("Parameterised queries prevent injection", ["parameter", "injection", "placeholder", "prepared"]),
            ("Indexes and query plans", ["index", "explain", "query plan", "scan"]),
            ("Transactions & isolation", ["transaction", "commit", "rollback", "isolation", "acid", "lock"]),
            ("Schema design / normalisation / constraints", ["normal", "foreign key", "constraint", "schema", "primary key"]),
            ("N+1 and query performance", ["n+1", "join", "batch", "eager", "lazy"])],
    "automated_testing": [("Unit vs integration vs end-to-end scope", ["unit", "integration", "end-to-end", "e2e"]),
                          ("Meaningful assertions on behaviour, edge cases", ["assert", "edge case", "boundary", "behavio"]),
                          ("Isolation: fixtures, mocks, test databases", ["fixture", "mock", "stub", "fake", "isolat"]),
                          ("Running tests in CI, coverage as a signal not a goal", ["ci", "pipeline", "coverage"]),
                          ("Testing failure paths", ["failure", "error", "exception", "negative"])],
    "ci_cd": [("Pipeline stages (install, lint, test, build, deploy)", ["stage", "lint", "test", "build", "deploy"]),
              ("Caching and reproducible builds", ["cache", "lock", "reproduc", "pin"]),
              ("Secrets management", ["secret", "env", "vault", "credential"]),
              ("Rollback & safe deploys", ["rollback", "canary", "blue", "green", "feature flag"])],
    "containerization": [("Image layering & small base images", ["layer", "slim", "alpine", "multi-stage", "base image"]),
                         ("Non-root, minimal attack surface", ["root", "user", "security", "minimal"]),
                         ("Config via env, 12-factor", ["env", "config", "12-factor", "twelve"]),
                         ("Health checks & orchestration", ["health", "compose", "kubernetes", "restart"])],
    "deployment": [("Environments & configuration", ["environment", "staging", "production", "config"]),
                   ("Health checks, monitoring, logs", ["health", "monitor", "log", "metric", "alert"]),
                   ("Rollback & zero-downtime strategy", ["rollback", "zero", "downtime", "blue", "canary"]),
                   ("Scaling & statelessness", ["scale", "stateless", "horizontal", "load balanc"])],
    "auth_security": [("Password hashing (bcrypt/argon2), never plain text", ["hash", "bcrypt", "argon", "salt"]),
                      ("Token/session lifecycle & expiry", ["token", "jwt", "expir", "refresh", "session"]),
                      ("Authorization checks per object", ["authoriz", "permission", "role", "object-level", "access control"]),
                      ("OWASP risks: injection, XSS, CSRF", ["owasp", "xss", "csrf", "injection"])],
    "caching": [("What to cache and invalidation strategy", ["invalidat", "ttl", "expir", "stale"]),
                ("Cache-aside vs write-through", ["cache-aside", "write-through", "write-back", "lazy"]),
                ("Consistency trade-offs", ["consisten", "stale", "trade-off"])],
    "concurrency": [("Race conditions & shared state", ["race", "shared", "lock", "mutex", "atomic"]),
                    ("Async I/O vs threads vs processes", ["async", "await", "thread", "process", "gil", "event loop"]),
                    ("Timeouts, cancellation, backpressure", ["timeout", "cancel", "backpressure", "queue"])],
    "system_design": [("Requirements & scale estimates", ["requirement", "scale", "qps", "estimate", "traffic"]),
                      ("Data model and storage choice", ["database", "schema", "storage", "sql", "nosql"]),
                      ("Bottlenecks, caching, queues", ["bottleneck", "cache", "queue", "async"]),
                      ("Failure modes & observability", ["failure", "retry", "monitor", "observab"])],
    "dsa": [("Clarify constraints & examples", ["constraint", "example", "edge"]),
            ("Choose data structure with justification", ["hash", "heap", "tree", "graph", "stack", "queue", "array"]),
            ("Time/space complexity analysis", ["o(", "complexity", "time", "space", "big o"]),
            ("Test with edge cases", ["edge", "empty", "test"])],
    "behavioral": [("Situation & task clearly framed", ["situation", "task", "when", "project", "team"]),
                   ("Specific actions you personally took", ["i ", "my ", "decided", "implemented", "led"]),
                   ("Measurable or concrete result", ["result", "outcome", "learned", "impact"]),
                   ("Reflection / what you'd do differently", ["differently", "learn", "next time", "reflect"])],
}
GENERIC_POINTS = [("Correct explanation of the core concept", ["because", "means", "works", "how"]),
                  ("Concrete example from experience", ["example", "project", "when i", "i built", "i used"]),
                  ("Trade-offs and limitations", ["trade-off", "tradeoff", "downside", "limitation", "instead"]),
                  ("Testing / verification approach", ["test", "verify", "check", "monitor"])]


def rubric_for(skill_key: str | None, qtype: str) -> dict[str, Any]:
    pts = KEY_POINTS.get("behavioral") if qtype == "behavioral" else KEY_POINTS.get(skill_key or "", GENERIC_POINTS)
    return {
        "dimensions": {
            "technical_correctness": "Statements are technically accurate; no misconceptions.",
            "depth": "Goes beyond definitions: mechanisms, trade-offs, failure modes.",
            "structure": "Logical order (e.g. STAR for behavioural, approach → details → trade-offs).",
            "relevance": "Addresses the question and the job requirement with concrete examples.",
            "communication": "Clear and concise; terms explained.",
        },
        "key_points": [p for p, _ in pts],
        "key_point_keywords": {p: kws for p, kws in pts},
        "scale": "1 (missing) – 5 (excellent)",
    }


# ------------------------------------------------------------------ LLM plan schema


class PlanQuestion(BaseModel):
    qtype: Literal["technical_fundamentals", "project_deep_dive", "system_scenario", "problem_solving", "testing_security", "behavioral",
                   "debugging", "gap_probe"]
    prompt: str = Field(min_length=30, max_length=900)
    target_skill_key: str | None = None
    source_kind: Literal["requirement", "evidence", "gap_row", "claim", "leetcode"]
    source_id: str
    difficulty: Literal["easy", "medium", "hard"] = "medium"
    key_points: list[str] = Field(min_length=2, max_length=6)
    prep_seconds: int = 30
    answer_seconds: int = 150

    @field_validator("prep_seconds", mode="before")
    @classmethod
    def _clamp_prep(cls, v):  # timer policy is the app's, not the model's: clamp instead of rejecting the plan
        try:
            return max(10, min(120, int(v)))
        except (TypeError, ValueError):
            return 30

    @field_validator("answer_seconds", mode="before")
    @classmethod
    def _clamp_answer(cls, v):
        try:
            return max(60, min(420, int(v)))
        except (TypeError, ValueError):
            return 150


class InterviewPlanLLM(BaseModel):
    questions: list[PlanQuestion] = Field(min_length=3, max_length=14)


@dataclass
class PlannedQuestion:
    qtype: str
    prompt: str
    target_skill_key: str | None
    requirement_id: str | None
    evidence_id: str | None
    assessment_row_id: str | None
    provenance: dict[str, Any]
    difficulty: str
    prep_seconds: int
    answer_seconds: int
    rubric: dict[str, Any]
    max_followups: int = 1
    code_context: dict[str, Any] | None = None


@dataclass
class PlanContext:
    job: dict[str, Any]
    requirements: list[dict[str, Any]]
    rows: list[dict[str, Any]]  # assessment rows (id, requirement_id, skill_key, verdict)
    evidence: list[dict[str, Any]]  # cited evidence (id, skill_key, repo, path, lines, reason, snippet, permalink)
    claims: list[dict[str, Any]]
    leetcode: dict[str, Any] | None
    allow_code: bool
    n_questions: int = 7
    ids: set[str] = field(default_factory=set)


def _tokens(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", s.lower()) if len(w) > 3}


def _dup(a: str, b: str) -> bool:
    ta, tb = _tokens(a), _tokens(b)
    return bool(ta and tb) and len(ta & tb) / len(ta | tb) > 0.6


def _timers(qtype: str, difficulty: str) -> tuple[int, int]:
    base = {"behavioral": 150, "system_scenario": 240, "problem_solving": 240, "project_deep_dive": 180, "debugging": 180}.get(qtype, 150)
    return (45 if qtype in ("system_scenario", "problem_solving") else 30), base + (60 if difficulty == "hard" else 0)


def template_plan(ctx: PlanContext) -> list[PlannedQuestion]:
    """Deterministic plan built only from the real posting, gaps, evidence and claims."""
    company = ctx.job.get("company") or "this company"
    title = ctx.job.get("title") or "this role"
    reqs = {r["id"]: r for r in ctx.requirements}
    rows = ctx.rows
    out: list[PlannedQuestion] = []

    def add(q: PlannedQuestion) -> None:
        if all(not _dup(q.prompt, o.prompt) for o in out):
            out.append(q)

    tech_rows = [r for r in rows if reqs.get(r["requirement_id"], {}).get("detector_supported")]
    rank = {"must_have": 0, "unclear": 1, "preferred": 2}
    must_rows = sorted(tech_rows or rows, key=lambda r: rank.get(reqs[r["requirement_id"]]["priority"], 3))
    demonstrated = [r for r in must_rows if r["verdict"] == "DEMONSTRATED"]
    gaps = [r for r in must_rows if r["verdict"] in ("NOT_DEMONSTRATED_IN_SCOPE", "CLAIMED_ONLY", "PARTIAL")]
    # 1. technical fundamentals on top must-have requirements
    for r in (demonstrated + gaps)[:2]:
        req = reqs[r["requirement_id"]]
        sk = req["skill_key"]
        p, a = _timers("technical_fundamentals", "medium")
        add(PlannedQuestion("technical_fundamentals",
                            f"The {title} posting at {company} asks for: “{req['quote'][:220]}”. Explain the core concepts of {skill_name(sk)} you would rely on "
                            f"in this role, and describe one trade-off you had to make when using it.",
                            sk, req["id"], None, r["id"], {"kind": "requirement", "id": req["id"], "quote": req["quote"], "source_url": req.get("source_url")},
                            "medium", p, a, rubric_for(sk, "technical_fundamentals")))
    # 2. project deep dive from real evidence
    for ev in ctx.evidence[:2]:
        p, a = _timers("project_deep_dive", "medium")
        add(PlannedQuestion("project_deep_dive",
                            f"In {ev['repo']} ({ev['path']}, lines {ev['start_line']}-{ev['end_line']}) the analysis found: {ev['reason']} "
                            f"Walk me through why you implemented it this way, what alternatives you considered, and how it behaves when something fails.",
                            ev["skill_key"], None, ev["id"], None, {"kind": "evidence", "id": ev["id"], "permalink": ev["permalink"]},
                            "medium", p, a, rubric_for(ev["skill_key"], "project_deep_dive"), max_followups=2))
    # 3. gap probes
    for r in gaps[:2]:
        req = reqs[r["requirement_id"]]
        sk = req["skill_key"]
        repo = ctx.evidence[0]["repo"] if ctx.evidence else "one of your projects"
        p, a = _timers("gap_probe", "medium")
        add(PlannedQuestion("gap_probe",
                            f"The role requires {skill_name(sk)} (“{req['quote'][:180]}”), which was {'only partially' if r['verdict'] == 'PARTIAL' else 'not'} "
                            f"demonstrated in your analysed repositories. How would you add it to {repo}? Describe the design, the code you would write, "
                            f"and the tests that would prove it works.",
                            sk, req["id"], None, r["id"], {"kind": "gap_row", "id": r["id"], "verdict": r["verdict"], "quote": req["quote"]},
                            "medium", p, a, rubric_for(sk, "gap_probe")))
    # 4. system scenario combining two real requirements
    tech = [reqs[r["requirement_id"]] for r in must_rows if reqs[r["requirement_id"]]["category"] in ("api_backend", "database", "architecture", "devops", "framework")]
    if len(tech) >= 2:
        a1, a2 = tech[0], tech[1]
        p, a = _timers("system_scenario", "hard")
        add(PlannedQuestion("system_scenario",
                            f"Design a feature for {company} that relies on both {skill_name(a1['skill_key'])} and {skill_name(a2['skill_key'])} as described in the posting. "
                            f"Cover the data model, API surface, failure handling and how you would deploy and monitor it.",
                            a1["skill_key"], a1["id"], None, None, {"kind": "requirement", "id": a1["id"], "also": a2["id"], "quote": a1["quote"]},
                            "hard", p, a, rubric_for("system_design", "system_scenario")))
    # 5. testing/security on the most relevant evidence or requirement
    tsec = next((reqs[r["requirement_id"]] for r in rows if reqs[r["requirement_id"]]["skill_key"] in ("automated_testing", "auth_security")), None)
    if tsec:
        p, a = _timers("testing_security", "medium")
        target = ctx.evidence[0] if ctx.evidence else None
        what = f"the code in {target['repo']}:{target['path']}" if target else "a service you have built"
        add(PlannedQuestion("testing_security",
                            f"The posting mentions “{tsec['quote'][:160]}”. How would you {'test' if tsec['skill_key'] == 'automated_testing' else 'secure'} {what}? "
                            f"Name concrete cases, tools and what you would assert or verify.",
                            tsec["skill_key"], tsec["id"], target["id"] if target else None, None, {"kind": "requirement", "id": tsec["id"], "quote": tsec["quote"]},
                            "medium", p, a, rubric_for(tsec["skill_key"], "testing_security")))
    # 6. debugging scenario anchored on a real route/evidence
    api_ev = next((e for e in ctx.evidence if e["skill_key"] in ("api_development", "sql")), None)
    if api_ev:
        p, a = _timers("debugging", "medium")
        add(PlannedQuestion("debugging",
                            f"After deployment, the code in {api_ev['repo']}:{api_ev['path']} (lines {api_ev['start_line']}-{api_ev['end_line']}) intermittently returns "
                            f"500 errors under load. Walk through how you would diagnose and fix it, step by step.",
                            api_ev["skill_key"], None, api_ev["id"], None, {"kind": "evidence", "id": api_ev["id"], "permalink": api_ev["permalink"]},
                            "medium", p, a, rubric_for(api_ev["skill_key"], "debugging")))
    # 7. problem solving if DSA is relevant
    dsa = next((r for r in ctx.requirements if r["skill_key"] == "dsa"), None)
    if dsa:
        weak = None
        if ctx.leetcode and ctx.leetcode.get("tag_counts"):
            core = ["dynamic-programming", "graph", "tree", "hash-table", "binary-search", "heap-priority-queue"]
            weak = min(core, key=lambda t: ctx.leetcode["tag_counts"].get(t, 0))
        p, a = _timers("problem_solving", "medium")
        topic = (weak or "graph").replace("-", " ")
        add(PlannedQuestion("problem_solving",
                            f"The posting asks for “{dsa['quote'][:140]}”. Talk through how you would solve a {topic} problem: given a list of service dependencies, "
                            f"return a valid deployment order or report a cycle. State the approach, data structures, complexity and edge cases.",
                            "dsa", dsa["id"], None, None, {"kind": "requirement", "id": dsa["id"], "quote": dsa["quote"],
                                                           "leetcode_basis": f"lowest solved count among core tags: {weak}" if weak else None},
                            "medium", p, a, rubric_for("dsa", "problem_solving")))
    # 8. behavioural STAR grounded in claims or collaboration requirement
    collab = next((r for r in ctx.requirements if r["category"] == "collaboration"), None)
    claim = next((c for c in ctx.claims if c.get("claim_type") in ("role", "project", "achievement")), None) or (ctx.claims[0] if ctx.claims else None)
    if claim or collab:
        p, a = _timers("behavioral", "medium")
        if claim:
            prompt = (f"Your {claim.get('evidence_class', 'profile').replace('CLAIMED_', '').lower()} mentions “{claim['text'][:160]}”. Using the STAR format, "
                      f"tell me about a specific situation in that work where something went wrong and what you did.")
            prov = {"kind": "claim", "id": claim["id"]}
        else:
            prompt = f"The posting says “{collab['quote'][:160]}”. Using STAR, describe a time you had to collaborate through a disagreement on a technical decision."
            prov = {"kind": "requirement", "id": collab["id"], "quote": collab["quote"]}
        add(PlannedQuestion("behavioral", prompt, collab["skill_key"] if collab else None, collab["id"] if collab else None, None, None,
                            prov, "medium", p, a, rubric_for(None, "behavioral")))
    return out[: ctx.n_questions]


def aliases(ctx: PlanContext) -> dict[str, tuple[str, str]]:
    """Short ids shown to the model (small local models copy R1/E2 far more reliably than 32-char hex)."""
    out: dict[str, tuple[str, str]] = {}
    for prefix, kind, items in (("R", "requirement", ctx.requirements), ("G", "gap_row", ctx.rows), ("E", "evidence", ctx.evidence),
                                ("C", "claim", ctx.claims[:10])):
        for i, it in enumerate(items, 1):
            out[f"{prefix}{i}"] = (kind, it["id"])
    return out


def validate_llm_plan(ctx: PlanContext, plan: InterviewPlanLLM) -> tuple[list[PlannedQuestion], list[str]]:
    reqs = {r["id"]: r for r in ctx.requirements}
    rows = {r["id"]: r for r in ctx.rows}
    evs = {e["id"]: e for e in ctx.evidence}
    claims = {c["id"]: c for c in ctx.claims}
    alias = aliases(ctx)
    out: list[PlannedQuestion] = []
    rejected: list[str] = []
    for q in plan.questions:
        kind, sid = q.source_kind, q.source_id.strip()
        if sid.upper() in alias:
            kind, sid = alias[sid.upper()]
        valid = (kind == "requirement" and sid in reqs) or (kind == "gap_row" and sid in rows) or (kind == "evidence" and sid in evs) \
            or (kind == "claim" and sid in claims) or (kind == "leetcode" and ctx.leetcode is not None)
        if not valid:
            rejected.append(f"unknown provenance {kind}:{sid}")
            continue
        if any(_dup(q.prompt, o.prompt) for o in out):
            rejected.append("duplicate question")
            continue
        if len(q.prompt.split()) < 8 or q.prompt.count("?") > 4:
            rejected.append("vague or multi-part question")
            continue
        req = reqs.get(sid) if kind == "requirement" else reqs.get(rows[sid]["requirement_id"]) if kind == "gap_row" else None
        ev = evs.get(sid) if kind == "evidence" else None
        skill = q.target_skill_key or (req["skill_key"] if req else ev["skill_key"] if ev else None)
        rub = rubric_for(skill, q.qtype)
        rub["key_points"] = q.key_points
        rub["key_point_keywords"] = {}
        prov: dict[str, Any] = {"kind": kind, "id": sid}
        if req:
            prov["quote"] = req["quote"]
            prov["source_url"] = req.get("source_url")
        if ev:
            prov["permalink"] = ev["permalink"]
        out.append(PlannedQuestion(q.qtype, q.prompt.strip(), skill, req["id"] if req else None, ev["id"] if ev else None,
                                   sid if kind == "gap_row" else None, prov, q.difficulty, q.prep_seconds, q.answer_seconds, rub,
                                   max_followups=2 if q.qtype in ("project_deep_dive", "system_scenario") else 1))
    return out[: ctx.n_questions], rejected


def llm_prompt(ctx: PlanContext) -> str:
    from ..integrations.llm import wrap_untrusted

    rev = {real: a for a, (_, real) in aliases(ctx).items()}
    lines = [f"JOB: {ctx.job.get('title')} at {ctx.job.get('company')} ({ctx.job.get('source_url') or 'user supplied'})",
             "Use ONLY these source ids (R = requirement, G = gap row, E = repository evidence, C = profile claim).", "REQUIREMENTS:"]
    for r in ctx.requirements:
        lines.append(f"- {rev[r['id']]} [{r['priority']}] {r['display_name']}: {wrap_untrusted('jd', r['quote'], 300)}")
    lines.append("GAP MATRIX ROWS:")
    for r in ctx.rows:
        lines.append(f"- {rev[r['id']]} for {rev.get(r['requirement_id'], '?')} skill={r['skill_key']} verdict={r['verdict']}")
    lines.append("REPOSITORY EVIDENCE:")
    for e in ctx.evidence:
        snippet = ("\n" + wrap_untrusted(e["path"], e.get("snippet", "")[:1200], 1200)) if ctx.allow_code else ""
        lines.append(f"- {rev[e['id']]} skill={e['skill_key']} {e['repo']}:{e['path']} L{e['start_line']}-{e['end_line']}: {e['reason']}{snippet}")
    if ctx.claims:
        lines.append("PROFILE CLAIMS:")
        for c in ctx.claims[:10]:
            lines.append(f"- {rev[c['id']]} ({c.get('claim_type')}): {wrap_untrusted('profile', c['text'], 200)}")
    if ctx.leetcode:
        lines.append(f"LEETCODE (problem-solving signal, source_kind=leetcode, source_id=LC): solved={ctx.leetcode.get('solved')} "
                     f"top tags={dict(list((ctx.leetcode.get('tag_counts') or {}).items())[:8])}")
    lines.append(f"Create {ctx.n_questions} questions. source_id must be one of the short ids above (e.g. R1, G2, E1).")
    return "\n".join(lines)


PLAN_SYSTEM = ("You are a senior technical interviewer preparing a realistic interview for THIS specific job. Produce a mix of question types: "
               "technical_fundamentals, project_deep_dive (about the candidate's actual cited code), system_scenario, problem_solving (if relevant), "
               "testing_security, behavioral (STAR), debugging, and gap_probe (for requirements that are not demonstrated). Every question must be "
               "answerable in a few minutes, specific to the job and the candidate's evidence, and must reference an existing source id "
               "(requirement / gap_row / evidence / claim) from the lists provided in source_id. Never write ids inside the question text: "
               "refer to code by repository/file/lines and to the job by quoting it. Provide 2-6 key points a strong answer covers. No generic filler.")
