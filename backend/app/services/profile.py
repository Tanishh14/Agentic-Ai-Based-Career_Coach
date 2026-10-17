"""Resume / LinkedIn-export claim extraction, claim audit and evidence-grounded resume bullets.

Claims are the lowest-weight evidence class (CLAIMED_RESUME / CLAIMED_PROFILE) and never
count as implementation proof. Bullets are drafted only from accepted repository evidence and
are rejected if they introduce numbers, scale or outcomes not present in that evidence.
"""
from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy import select

from ..agents.jd_extractor import validate_quote
from ..analyzers.taxonomy import find_skill_mentions, normalize_term, skill_name
from ..database import session_scope
from ..errors import AppError, NotConfigured
from ..integrations.llm import LLMFailed, complete_json, llm_available, wrap_untrusted
from ..models import EvidenceRecord, ProfileClaim, ProfileDocument
from ..security import sha256_hex
from .documents import ExtractedDoc, page_for_offset, section_for_offset
from .events import audit, emit


class LLMClaim(BaseModel):
    claim_type: Literal["skill", "role", "project", "certification", "achievement"]
    text: str = Field(min_length=2, max_length=300)
    quote: str = Field(min_length=3, max_length=500)
    skill_terms: list[str] = Field(default_factory=list, max_length=8)


class LLMProfile(BaseModel):
    claims: list[LLMClaim] = Field(max_length=60)


def _evidence_class(kind: str) -> str:
    return "CLAIMED_PROFILE" if kind == "linkedin_export" else "CLAIMED_RESUME"


async def ingest_profile(user_id: str, kind: str, doc: ExtractedDoc, filename: str | None, mime: str | None, size: int) -> dict[str, Any]:
    if kind not in ("resume", "linkedin_export", "pasted_profile"):
        raise AppError("bad_kind", "kind must be resume, linkedin_export or pasted_profile", status=422)
    text = doc.text
    if len(text.strip()) < 30:
        raise AppError("empty_profile", "No readable text found in the document.", status=422, action="Paste the text instead.")
    cls = _evidence_class(kind)
    claims: list[dict[str, Any]] = []
    seen_skill: dict[str, int] = {}
    for sk, a, b, matched in find_skill_mentions(text):
        if seen_skill.get(sk.key, 0) >= 3:
            continue
        seen_skill[sk.key] = seen_skill.get(sk.key, 0) + 1
        ls = text.rfind("\n", 0, a) + 1
        le = text.find("\n", b)
        le = len(text) if le < 0 else le
        if le - ls > 240:
            ls, le = max(ls, a - 100), min(le, b + 100)
        claims.append({"claim_type": "skill", "text": text[ls:le].strip()[:300], "skill_key": sk.key, "span": (ls, le), "method": "taxonomy_match", "conf": 0.7})
    warning = doc.warning
    llm_info = None
    if llm_available(user_id):
        try:
            res = await complete_json(user_id, "Extract the candidate's claims from this resume/profile: skills, roles (title @ company, dates), projects, "
                                               "certifications and achievements. For each, copy an exact verbatim quote from the document. Do not infer or embellish.",
                                      wrap_untrusted("profile", text, 14000), LLMProfile, max_tokens=4000)
            kept = 0
            for c in res.data.claims:
                span = validate_quote(text, c.quote)
                if span is None:
                    continue
                keys = {normalize_term(t)[0] for t in c.skill_terms} | {m[0].key for m in find_skill_mentions(text[span[0]:span[1]])}
                keys = {k for k in keys if not k.startswith("custom:")} or {None}
                for k in keys:
                    claims.append({"claim_type": c.claim_type, "text": c.text[:300], "skill_key": k, "span": span, "method": "llm_validated", "conf": 0.75})
                kept += 1
            llm_info = {"provider": res.provider, "model": res.model, "proposed": len(res.data.claims), "kept": kept}
        except (LLMFailed, NotConfigured) as e:
            warning = (warning + " " if warning else "") + f"LLM structuring unavailable ({e.message[:120]}); taxonomy-only claims."
    with session_scope() as db:
        for d in db.execute(select(ProfileDocument).where(ProfileDocument.user_id == user_id, ProfileDocument.kind == kind, ProfileDocument.active.is_(True))).scalars():
            d.active = False
        d = ProfileDocument(user_id=user_id, kind=kind, filename=filename, mime=mime, size=size, content_hash=sha256_hex(text), extracted_text=text,
                            page_offsets=doc.page_offsets, parse_method=doc.method, parse_warning=warning, structured={"llm": llm_info})
        db.add(d)
        db.flush()
        dedupe = set()
        for c in claims:
            key = (c["claim_type"], c["skill_key"], c["span"])
            if key in dedupe:
                continue
            dedupe.add(key)
            db.add(ProfileClaim(document_id=d.id, evidence_class=cls, claim_type=c["claim_type"], text=c["text"], skill_key=c["skill_key"],
                                page=page_for_offset(doc.page_offsets, c["span"][0]), span_start=c["span"][0], span_end=c["span"][1],
                                section=section_for_offset(text, c["span"][0]), extraction_method=c["method"], confidence=c["conf"]))
        doc_id = d.id
        audit("profile_uploaded", {"document_id": doc_id, "kind": kind, "size": size}, user_id, db=db)
        emit("profile_parsed", {"document_id": doc_id, "claims": len(dedupe)}, user_id, db=db)
    return {"document_id": doc_id, "claims": len(dedupe), "warning": warning, "llm": llm_info}


def claim_audit(user_id: str) -> list[dict[str, Any]]:
    with session_scope() as db:
        rows = db.execute(select(ProfileClaim, ProfileDocument).join(ProfileDocument, ProfileClaim.document_id == ProfileDocument.id)
                          .where(ProfileDocument.user_id == user_id, ProfileDocument.active.is_(True))).all()
        ev = db.execute(select(EvidenceRecord).where(EvidenceRecord.user_id == user_id, EvidenceRecord.status == "current",
                                                     EvidenceRecord.verification_status.in_(("accepted", "ambiguous")))).scalars().all()
        by_skill: dict[str, list[EvidenceRecord]] = {}
        for e in ev:
            by_skill.setdefault(e.skill_key, []).append(e)
        out = []
        for c, d in rows:
            items = by_skill.get(c.skill_key or "", [])
            strong = [e for e in items if e.verification_status == "accepted" and e.depth in ("intermediate", "substantial") and e.source_type != "DEPENDENCY_DECLARATION"]
            weak = [e for e in items if e not in strong and e.source_type != "DEPENDENCY_DECLARATION"]
            if not c.skill_key:
                status = "not a code-verifiable claim"
            elif strong:
                status = "claimed, portfolio evidence found"
            elif weak:
                status = "partially supported by supplied scope"
            else:
                status = "not demonstrated in current GitHub scope"
            out.append({"id": c.id, "claim_type": c.claim_type, "text": c.text, "skill_key": c.skill_key, "skill": skill_name(c.skill_key) if c.skill_key else None,
                        "evidence_class": c.evidence_class, "document": d.filename or d.kind, "document_id": d.id, "page": c.page, "section": c.section,
                        "span": [c.span_start, c.span_end], "extraction_method": c.extraction_method, "status": status,
                        "supporting_evidence": [{"id": e.id, "permalink": e.permalink, "path": e.path, "repo": e.repo_full_name, "reason": e.reason} for e in (strong or weak)[:3]],
                        "next_step": None if strong or not c.skill_key else "Add a public repository that shows this, include a private repo in scope, or mark it out of scope."})
        return out


class Bullet(BaseModel):
    text: str = Field(min_length=20, max_length=260)
    evidence_ids: list[str] = Field(min_length=1, max_length=4)


class Bullets(BaseModel):
    bullets: list[Bullet] = Field(max_length=8)


def _numbers(s: str) -> set[str]:
    return set(re.findall(r"\d+(?:[.,]\d+)?%?", s))


async def resume_bullets(user_id: str) -> dict[str, Any]:
    with session_scope() as db:
        ev = db.execute(select(EvidenceRecord).where(EvidenceRecord.user_id == user_id, EvidenceRecord.status == "current",
                                                     EvidenceRecord.verification_status == "accepted",
                                                     EvidenceRecord.depth.in_(("intermediate", "substantial")),
                                                     EvidenceRecord.source_type.in_(("CODE_IMPLEMENTATION", "CODE_TEST", "CODE_CONFIG")))).scalars().all()
        items = [{"id": e.id, "skill": skill_name(e.skill_key), "repo": e.repo_full_name, "path": e.path, "reason": e.reason, "permalink": e.permalink,
                  "lines": f"{e.start_line}-{e.end_line}"} for e in sorted(ev, key=lambda e: -e.confidence)][:24]
    if not items:
        return {"bullets": [], "generator": None, "note": "No accepted intermediate/substantial evidence yet — nothing to ground bullets in."}
    ids = {i["id"]: i for i in items}
    if llm_available(user_id):
        try:
            facts = "\n".join(f"- id={i['id']} [{i['skill']}] {i['repo']}:{i['path']} — {i['reason']}" for i in items)
            res = await complete_json(user_id, "Draft concise resume bullets ONLY from these verified evidence facts. Each bullet must cite the evidence ids it "
                                               "relies on. Do NOT invent metrics, scale, users, impact, ownership, team size or technologies not in the facts.",
                                      facts, Bullets, max_tokens=1500)
            out = []
            for b in res.data.bullets:
                cited = [ids[x] for x in b.evidence_ids if x in ids]
                if not cited:
                    continue
                allowed = set().union(*(_numbers(c["reason"] + c["path"] + c["lines"]) for c in cited))
                if not _numbers(b.text) <= allowed:
                    continue
                out.append({"text": b.text, "evidence": cited})
            return {"bullets": out, "generator": f"llm:{res.model}", "note": "Bullets with unsupported numbers were discarded. Review before using."}
        except (LLMFailed, NotConfigured):
            pass
    by_skill: dict[str, list[dict]] = {}
    for i in items:
        by_skill.setdefault(i["skill"], []).append(i)
    out = [{"text": f"Implemented {sk.lower()} in {v[0]['repo']} — {v[0]['reason'].rstrip('.')}.", "evidence": v[:2]} for sk, v in list(by_skill.items())[:6]]
    return {"bullets": out, "generator": "template (no LLM configured)", "note": "Drafts restate verified evidence only. Review before using."}
