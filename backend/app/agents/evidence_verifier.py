"""Adversarial critic: tries to disprove every candidate evidence item.

Checks (all deterministic): valid line mapping at the pinned commit, generated/vendor
code, README/comment-only content, empty/placeholder bodies, trivial tests, SQL without
execution, unreferenced functions. An optional LLM corroboration pass can only *lower*
confidence or mark evidence ambiguous — it can never create or upgrade evidence.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from ..analyzers.call_graph import ReferenceIndex
from ..analyzers.findings import DEPTH_ORDER, Finding
from ..analyzers.lexer import strip_c_comments, strip_hash_comments
from ..analyzers.taxonomy import skill_name

BASE_CONFIDENCE = {
    ("meaningful_implementation", "substantial"): 0.92,
    ("meaningful_implementation", "intermediate"): 0.82,
    ("meaningful_implementation", "basic"): 0.66,
    ("tests_found", "substantial"): 0.9,
    ("tests_found", "intermediate"): 0.82,
    ("tests_found", "basic"): 0.65,
    ("code_usage", "substantial"): 0.78,
    ("code_usage", "intermediate"): 0.68,
    ("code_usage", "basic"): 0.55,
    ("config_present", "substantial"): 0.8,
    ("config_present", "intermediate"): 0.68,
    ("config_present", "basic"): 0.5,
    ("dependency_detected", "trivial"): 0.25,
}
LEXICAL_DETECTOR_PREFIXES = ("js_", "nextjs_", "nestjs_", "java_", "kotlin_", "spring_", "jpa_", "jdbc_", "go_", "gorm_", "aspnet_", "ef_", "dotnet_",
                             "rust_", "laravel_", "php", "rails_", "active_", "rspec_", "jvm_", "csharp_", "ruby_", "swift_", "scala_", "cpp_", "c_")


@dataclass
class Verified:
    status: str  # accepted | rejected | ambiguous
    depth: str
    confidence: float
    reachability: str
    notes: list[str] = field(default_factory=list)
    snippet: str = ""


def _strip_for_lang(path: str, snippet: str) -> str:
    if path.endswith(".py") or path.lower().endswith(("dockerfile", ".yml", ".yaml", ".toml", ".sh", ".rb", "procfile")):
        lines = []
        for ln in strip_hash_comments(snippet).split("\n"):
            lines.append(ln)
        return "\n".join(lines)
    return strip_c_comments(snippet)


def verify_finding(f: Finding, path: str, file_text: str, file_kind: str, index: ReferenceIndex | None) -> Verified:
    notes: list[str] = []
    lines = file_text.split("\n")
    n = len(lines)
    if f.start_line < 1 or f.end_line < f.start_line or f.start_line > n:
        return Verified("rejected", "trivial", 0.0, "unknown", ["invalid line mapping: cited lines do not exist in the file at this commit"], "")
    end = min(f.end_line, n)
    snippet = "\n".join(lines[f.start_line - 1 : end])
    if not _strip_for_lang(path, snippet).strip() and f.source_type != "DEPENDENCY_DECLARATION":
        return Verified("rejected", "trivial", 0.0, "unknown", ["cited lines contain only comments/whitespace"], snippet)
    if file_kind in ("generated", "vendor"):
        return Verified("rejected", "trivial", 0.0, "unknown", [f"{file_kind} code is not candidate-authored evidence"], snippet)
    if file_kind == "docs":
        return Verified("rejected", "trivial", 0.0, "unknown", ["documentation/README text is not implementation evidence"], snippet)

    status = f.candidate_status
    depth = f.depth
    if status == "rejected":
        notes.append(f"detector rejected: {f.reason}")
        return Verified("rejected", "trivial", 0.05, "unknown", notes, snippet)

    reach = index.reachability(f.symbol, path, f.node_type) if index else "unknown"
    conf = BASE_CONFIDENCE.get((f.signal_level, depth), 0.5 if depth != "trivial" else 0.2)
    if f.detector.startswith(LEXICAL_DETECTOR_PREFIXES):
        conf -= 0.05
        notes.append("lexical detector (comment-stripped pattern + bracket matching), not a full parser")
    if reach == "unreferenced" and f.signal_level in ("meaningful_implementation", "code_usage") and f.source_type == "CODE_IMPLEMENTATION":
        conf -= 0.25
        notes.append(f"`{f.symbol}` is not referenced anywhere else in the analysed scope (possibly dead code; static analysis is approximate)")
        if DEPTH_ORDER.get(depth, 0) > DEPTH_ORDER["basic"]:
            depth = "basic"
        status = "ambiguous"
    if status == "ambiguous" and not any("not referenced" in x for x in notes):
        notes.append(f.reason)
    if f.meta.get("sut_calls") == 0:
        status = "ambiguous"
        notes.append("test does not call any code under test")
    if f.source_type == "DEPENDENCY_DECLARATION":
        notes.append("dependency declaration only — proves the package is listed, not that it is used")
    conf = round(max(0.0, min(0.97, conf)), 2)
    return Verified(status, depth, conf, reach, notes, snippet)


# ------------------------------------------------------------------ optional LLM corroboration


class CorroborationItem(BaseModel):
    id: str
    supports_claim: bool
    concerns: list[str] = Field(default_factory=list, max_length=4)


class CorroborationResult(BaseModel):
    items: list[CorroborationItem]


async def llm_corroborate(user_id: str, batch: list[dict]) -> dict[str, CorroborationItem]:
    """batch: [{id, skill_key, path, start_line, end_line, snippet, reason}] — returns verdicts by id.

    The model only sees the supplied snippet and can only raise concerns.
    """
    from ..integrations.llm import complete_json, wrap_untrusted

    if not batch:
        return {}
    parts = []
    for b in batch:
        parts.append(f"ID {b['id']} | skill: {skill_name(b['skill_key'])} | {b['path']}:{b['start_line']}-{b['end_line']} | detector says: {b['reason']}\n"
                     + wrap_untrusted(f"{b['path']}", b["snippet"][:2500]))
    system = ("You are a strict code reviewer verifying portfolio evidence. For each snippet decide whether the code itself (not comments) "
              "genuinely implements the stated skill. Say supports_claim=false for placeholders, dead stubs, copied boilerplate with no logic, "
              "or code unrelated to the skill. Base your judgement ONLY on the snippet shown.")
    res = await complete_json(user_id, system, "\n\n".join(parts), CorroborationResult, max_tokens=1500)
    return {it.id: it for it in res.data.items}
