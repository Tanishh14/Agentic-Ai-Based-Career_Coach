"""Requirement extraction with verbatim quote validation.

1. Deterministic taxonomy matcher finds skills in the stored JD text and quotes the
   exact bullet/sentence (character offsets into the stored text).
2. If an LLM provider is configured, it proposes additional/renamed requirements in a
   strict schema. Every proposed quote must exist in the stored text (exact, or
   whitespace-normalised with offsets mapped back); otherwise the candidate is rejected.
3. Synonyms are deduplicated by normalised skill key; original wording is preserved.
No requirement count is forced: sparse postings yield fewer rows plus a warning.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field

from ..analyzers.taxonomy import TAXONOMY_VERSION, detector_supported, find_skill_mentions, get_skill, normalize_term

EXTRACTOR_VERSION = f"jd-extractor-2.1/taxonomy-{TAXONOMY_VERSION}"
MAX_REQUIREMENTS = 15

PREFERRED_HDR = re.compile(r"(nice[- ]to[- ]have|preferred|bonus|plus(es)?\b|desir|good to have|would be great|extra credit|optional)", re.I)
REQUIRED_HDR = re.compile(r"(requirement|qualification|must[- ]have|what you('ll)? (need|bring)|you (have|bring|should)|who you are|"
                          r"skills|what we('re)? looking for|about you|minimum|basic qualifications|required)", re.I)
NEUTRAL_HDR = re.compile(r"(responsibilit|what you('ll)? do|the role|about (the|us)|benefits|perks|we offer|day[- ]to[- ]day)", re.I)
PREFERRED_CUE = re.compile(r"(nice to have|is a plus|are a plus|a bonus|preferred|ideally|desirable|familiarity with .{0,40} (a )?plus|bonus points|good to have)", re.I)
REQUIRED_CUE = re.compile(r"\b(must|required|requirement|need to|you have|strong|proficien\w+|solid|minimum|at least|hands-on|experience (with|in))\b", re.I)


@dataclass
class ExtractedRequirement:
    skill_key: str
    display_name: str
    original_term: str
    category: str
    priority: str
    quote: str
    char_start: int
    char_end: int
    method: str
    confidence: float
    detector_supported: bool


@dataclass
class ExtractionResult:
    requirements: list[ExtractedRequirement]
    warnings: list[str] = field(default_factory=list)
    rejected: list[dict] = field(default_factory=list)
    llm: dict | None = None


def _line_bounds(text: str, pos: int) -> tuple[int, int]:
    s = text.rfind("\n", 0, pos) + 1
    e = text.find("\n", pos)
    return s, (len(text) if e < 0 else e)


def _quote_span(text: str, a: int, b: int) -> tuple[int, int]:
    """Smallest bullet/sentence containing [a,b), trimmed, at most ~280 chars."""
    ls, le = _line_bounds(text, a)
    if le - ls > 280:
        # sentence inside a long paragraph
        ss = max(text.rfind(". ", ls, a), text.rfind("; ", ls, a))
        ss = ls if ss < 0 else ss + 2
        se_candidates = [i for i in (text.find(". ", b, le), text.find("; ", b, le)) if i >= 0]
        se = min(se_candidates) + 1 if se_candidates else le
        ls, le = ss, min(se, ss + 400)
    while ls < le and text[ls] in " \t•*-–·>":
        ls += 1
    while le > ls and text[le - 1] in " \t":
        le -= 1
    return ls, le


def _section_priority(text: str, pos: int) -> str | None:
    """Look back for the nearest heading-like line and classify it (None if no recognised heading)."""
    idx = pos
    for _ in range(40):
        prev_end = text.rfind("\n", 0, idx)
        if prev_end < 0:
            break
        prev_start = text.rfind("\n", 0, prev_end) + 1
        line = text[prev_start:prev_end].strip()
        idx = prev_start
        if not line:
            continue
        is_heading = (len(line) <= 70 and (line.endswith(":") or line.isupper() or not re.search(r"[.!?,;]$", line)) and len(line.split()) <= 9)
        if not is_heading:
            continue
        if PREFERRED_HDR.search(line):
            return "preferred"
        if REQUIRED_HDR.search(line):
            return "must_have"
        if NEUTRAL_HDR.search(line):
            return "unclear"
    return None


def _priority(text: str, qs: int, qe: int) -> tuple[str, int]:
    """(priority, strength): strength 3 = explicit cue in the quote, 2 = section heading, 1 = keyword cue, 0 = none."""
    quote = text[qs:qe]
    if PREFERRED_CUE.search(quote):
        return "preferred", 3
    sec = _section_priority(text, qs)
    if sec is not None:
        return sec, 2
    if REQUIRED_CUE.search(quote):
        return "must_have", 1
    return "unclear", 0


def deterministic_extract(text: str) -> list[ExtractedRequirement]:
    found: dict[str, ExtractedRequirement] = {}
    strengths: dict[str, int] = {}
    mentions: dict[str, int] = {}
    for skill, a, b, matched in find_skill_mentions(text):
        mentions[skill.key] = mentions.get(skill.key, 0) + 1
        qs, qe = _quote_span(text, a, b)
        prio, strength = _priority(text, qs, qe)
        cand = ExtractedRequirement(skill.key, skill.name, matched, skill.category, prio, text[qs:qe], qs, qe, "taxonomy_match",
                                    0.7, detector_supported(skill.key))
        cur = found.get(skill.key)
        rank = {"must_have": 0, "unclear": 1, "preferred": 2}
        if cur is None or rank[prio] < rank[cur.priority] or (rank[prio] == rank[cur.priority] and strength > strengths[skill.key]):
            found[skill.key] = cand
            strengths[skill.key] = strength
    for k, r in found.items():
        r.confidence = min(0.9, 0.6 + 0.08 * mentions.get(k, 1))
    return list(found.values())


# ------------------------------------------------------------------ LLM extraction


class LLMRequirement(BaseModel):
    term: str = Field(min_length=1, max_length=80, description="Skill/technology/competency as worded in the JD")
    quote: str = Field(min_length=3, max_length=400, description="Exact verbatim substring of the JD that states this requirement")
    priority: Literal["must_have", "preferred", "unclear"]
    category: Literal["language", "framework", "api_backend", "database", "testing", "devops", "architecture", "security",
                      "cs_fundamentals", "domain", "collaboration", "other"] = "other"
    confidence: float = Field(ge=0, le=1, default=0.7)


class LLMExtraction(BaseModel):
    requirements: list[LLMRequirement] = Field(max_length=25)
    company: str | None = None
    title: str | None = None


def _norm_ws(s: str) -> tuple[str, list[int]]:
    out, idx = [], []
    prev_space = False
    for i, ch in enumerate(s):
        if ch.isspace():
            if not prev_space:
                out.append(" ")
                idx.append(i)
            prev_space = True
        else:
            out.append(ch.lower())
            idx.append(i)
            prev_space = False
    return "".join(out), idx


def validate_quote(text: str, quote: str) -> tuple[int, int] | None:
    """Return (start, end) offsets of the quote in text: exact match first, then
    whitespace/case-normalised match mapped back to original offsets. None if absent."""
    q = quote.strip().strip('"“”').strip()
    if not q:
        return None
    i = text.find(q)
    if i >= 0:
        return i, i + len(q)
    nt, idx = _norm_ws(text)
    nq, _ = _norm_ws(q)
    nq = nq.strip()
    j = nt.find(nq)
    if j < 0 or not nq:
        return None
    start = idx[j]
    end = idx[min(j + len(nq) - 1, len(idx) - 1)] + 1
    return start, end


def merge_llm(text: str, base: list[ExtractedRequirement], llm: LLMExtraction) -> tuple[list[ExtractedRequirement], list[dict]]:
    by_key = {r.skill_key: r for r in base}
    rejected: list[dict] = []
    for item in llm.requirements:
        span = validate_quote(text, item.quote)
        if span is None:
            rejected.append({"term": item.term, "quote": item.quote[:200], "reason": "quote not found verbatim in the stored job text"})
            continue
        key, name, cat = normalize_term(item.term)
        quote_text = text[span[0]:span[1]]
        term_in_quote = item.term.lower() in quote_text.lower() or any(m[0].key == key for m in find_skill_mentions(quote_text))
        if not term_in_quote:
            rejected.append({"term": item.term, "quote": item.quote[:200], "reason": "quote does not mention the requirement"})
            continue
        if key.startswith("custom:"):
            cat = item.category
        existing = by_key.get(key)
        if existing is not None:
            if existing.method == "taxonomy_match":
                existing.method = "taxonomy_match+llm_validated"
                existing.original_term = item.term[:200]
                if item.priority != "unclear" and existing.priority == "unclear":
                    existing.priority = item.priority
                existing.confidence = min(0.95, max(existing.confidence, item.confidence))
            continue
        sk = get_skill(key)
        by_key[key] = ExtractedRequirement(key, sk.name if sk else name, item.term[:200], sk.category if sk else cat,
                                           item.priority if not PREFERRED_CUE.search(quote_text) else "preferred",
                                           quote_text, span[0], span[1], "llm_validated", round(min(0.9, item.confidence), 2), detector_supported(key))
    return list(by_key.values()), rejected


def finalize(reqs: list[ExtractedRequirement]) -> list[ExtractedRequirement]:
    rank = {"must_have": 0, "unclear": 1, "preferred": 2}
    reqs.sort(key=lambda r: (rank.get(r.priority, 3), not r.detector_supported, -r.confidence, r.char_start))
    return reqs[:MAX_REQUIREMENTS]


async def extract(user_id: str, text: str, title_hint: str | None = None, run=None) -> ExtractionResult:
    from ..errors import NotConfigured
    from ..integrations.llm import LLMFailed, complete_json, wrap_untrusted

    base = deterministic_extract(text)
    result = ExtractionResult(requirements=base)
    try:
        if run is not None:
            run.charge_llm()
        system = ("Extract the concrete skill/technology/competency requirements from this job or internship posting. "
                  "For each, copy an EXACT verbatim quote from the posting that states it (do not paraphrase), the term as worded, "
                  "priority must_have/preferred/unclear (use 'preferred' only if the posting marks it as a plus/nice-to-have), and category. "
                  "Only include requirements actually present; do not invent or pad. Return 0-20 items.")
        res = await complete_json(user_id, system, wrap_untrusted("job_posting", text, 14000), LLMExtraction, max_tokens=3000)
        merged, rejected = merge_llm(text, base, res.data)
        result.requirements = merged
        result.rejected = rejected
        result.llm = {"provider": res.provider, "model": res.model, "attempts": res.attempts, "proposed": len(res.data.requirements),
                      "rejected": len(rejected)}
        if rejected:
            result.warnings.append(f"{len(rejected)} LLM-proposed requirement(s) were rejected because their quotes could not be verified in the job text.")
    except NotConfigured:
        result.warnings.append("No LLM provider configured: requirements come from the deterministic skill taxonomy only (quotes are still verbatim).")
    except LLMFailed as e:
        result.warnings.append(f"LLM extraction failed ({e.message}); kept deterministic taxonomy matches only.")
    except Exception as e:  # noqa: BLE001 - budget or unexpected provider error; deterministic data retained
        result.warnings.append(f"LLM extraction skipped: {type(e).__name__}: {str(e)[:200]}")
    result.requirements = finalize(result.requirements)
    if len(result.requirements) < 3:
        result.warnings.append(f"Sparse posting: only {len(result.requirements)} verifiable requirement(s) found. Nothing was invented — "
                               "paste a fuller description if the posting has more detail.")
    if len(text) < 300:
        result.warnings.append("The job text is very short; extraction quality is limited.")
    return result
