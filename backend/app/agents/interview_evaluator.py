"""Per-answer evaluation against the question's rubric.

LLM path: strict schema, scores 1-5 per dimension, strengths, missing points, one concrete
correction, a model answer outline and an optional follow-up question.
Heuristic path (no LLM configured): keyword coverage of the rubric key points only — clearly
labelled; technical correctness is reported as "not assessed".
Scores are coaching signals, never hiring predictions. Delivery metrics are approximate and
computed only from the actual transcript/timing; they are never used as a competence proxy.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field

EVALUATOR_VERSION = "eval-2.0"
FILLERS = re.compile(r"\b(um+|uh+|erm|you know|basically|kind of|sort of|like,)\b", re.I)


class Scores(BaseModel):
    technical_correctness: int = Field(ge=1, le=5)
    depth: int = Field(ge=1, le=5)
    structure: int = Field(ge=1, le=5)
    relevance: int = Field(ge=1, le=5)
    communication: int = Field(ge=1, le=5)


class LLMEvaluation(BaseModel):
    scores: Scores
    strengths: list[str] = Field(default_factory=list, max_length=5)
    missing_points: list[str] = Field(default_factory=list, max_length=6)
    correction: str = Field(min_length=5, max_length=600)
    model_answer: str = Field(min_length=20, max_length=2500)
    followup_question: str | None = Field(default=None, max_length=500)


@dataclass
class EvalOutcome:
    scores: dict[str, Any]
    overall: float
    strengths: list[str]
    missing_points: list[str]
    correction: str
    model_answer: str
    followup: str | None
    provider: str
    model: str


def delivery_metrics(transcript: str, duration_s: float | None, source: str) -> dict[str, Any]:
    words = len(re.findall(r"\b\w+\b", transcript or ""))
    fillers = len(FILLERS.findall(transcript or ""))
    out: dict[str, Any] = {"words": words, "label": "approximate; from transcript text only, not a competence measure"}
    if source == "audio" and duration_s and duration_s > 5:
        out["duration_seconds"] = round(duration_s, 1)
        out["words_per_minute"] = round(words / (duration_s / 60), 1)
        out["filler_count"] = fillers
    elif source == "typed":
        out["note"] = "typed answer — speaking pace not applicable"
    return out


def heuristic_evaluate(question: dict[str, Any], transcript: str) -> EvalOutcome:
    rub = question.get("rubric") or {}
    kws: dict[str, list[str]] = rub.get("key_point_keywords") or {}
    text = (transcript or "").lower()
    covered, missing = [], []
    for point in rub.get("key_points") or []:
        words = kws.get(point) or [w for w in re.findall(r"[a-z]{5,}", point.lower())][:4]
        if any(w in text for w in words):
            covered.append(point)
        else:
            missing.append(point)
    n = max(1, len(covered) + len(missing))
    cov = len(covered) / n
    wc = len(text.split())
    sentences = max(1, len(re.findall(r"[.!?]", transcript or "")))
    rel = 1 + round(4 * cov)
    depth = 1 + min(4, round(4 * cov * min(1.0, wc / 150)))
    structure = 3 if sentences >= 3 else 2 if wc > 40 else 1
    if question.get("qtype") == "behavioral" and all(k in text for k in ("situation", "result")):
        structure = 4
    comm = 3 if 40 <= wc <= 450 else 2
    scores = {"technical_correctness": None, "depth": depth, "structure": structure, "relevance": rel, "communication": comm,
              "method": "heuristic keyword coverage (no LLM configured) — technical correctness not assessed"}
    vals = [v for k, v in scores.items() if isinstance(v, int)]
    overall = round(sum(vals) / len(vals) * 20, 1) if vals and wc > 0 else 0.0
    return EvalOutcome(scores, overall, [f"Covered: {p}" for p in covered][:5], missing,
                       f"Practise covering: {missing[0]}." if missing else "Add a concrete example and a trade-off to deepen the answer.",
                       "Key points a strong answer covers:\n- " + "\n- ".join(rub.get("key_points") or []), None, "heuristic", EVALUATOR_VERSION)


EVAL_SYSTEM = ("You are a fair, rigorous technical interviewer giving coaching feedback. Score the candidate's answer against the rubric "
               "dimensions (1-5). Judge only the content of the transcript; do not penalise accent, grammar slips typical of speech "
               "transcription, or disability-related speech patterns. Be specific: cite what was said. 'missing_points' must be tied to the "
               "rubric key points or the job requirement. Give exactly one concrete correction to practise next, a concise technically "
               "correct model answer outline, and optionally ONE follow-up question that probes the most important gap in this answer "
               "(null if the answer was complete or follow-ups are not allowed).")


async def llm_evaluate(user_id: str, question: dict[str, Any], transcript: str, allow_followup: bool) -> EvalOutcome:
    from ..integrations.llm import complete_json, wrap_untrusted

    rub = question.get("rubric") or {}
    ctx = [f"QUESTION TYPE: {question.get('qtype')}", f"QUESTION: {question.get('prompt')}",
           f"RUBRIC DIMENSIONS: {rub.get('dimensions')}", f"KEY POINTS: {rub.get('key_points')}"]
    prov = question.get("provenance") or {}
    if prov.get("quote"):
        ctx.append("JOB REQUIREMENT: " + wrap_untrusted("jd", prov["quote"], 400))
    cc = question.get("code_context")
    if cc and cc.get("snippet"):
        ctx.append(f"CANDIDATE CODE ({cc.get('repo')}:{cc.get('path')} L{cc.get('start_line')}-{cc.get('end_line')} @ {str(cc.get('commit_sha'))[:12]}):\n"
                   + wrap_untrusted(cc.get("path", "code"), cc["snippet"], 3000))
    ch = question.get("challenge")
    if ch and ch.get("diff"):
        ctx.append("HYPOTHETICAL BUG DIFF SHOWN TO CANDIDATE:\n" + wrap_untrusted("diff", ch["diff"], 2500) +
                   f"\nINTENDED BUG (for grading): {ch.get('bug_description')}")
    ctx.append(f"FOLLOW-UP ALLOWED: {'yes' if allow_followup else 'no'}")
    ctx.append("ANSWER TRANSCRIPT:\n" + wrap_untrusted("transcript", transcript or "(no answer)", 8000))
    res = await complete_json(user_id, EVAL_SYSTEM, "\n\n".join(ctx), LLMEvaluation, max_tokens=2000)
    e: LLMEvaluation = res.data
    sc = e.scores.model_dump()
    overall = round(sum(sc.values()) / len(sc) * 20, 1)
    if not (transcript or "").strip():
        overall = 0.0
    return EvalOutcome({**sc, "method": "llm rubric evaluation"}, overall, e.strengths, e.missing_points, e.correction, e.model_answer,
                       e.followup_question if allow_followup else None, res.provider, res.model)
