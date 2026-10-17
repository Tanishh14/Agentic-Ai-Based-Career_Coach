"""Profile/resume claims and LeetCode signals."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, Form, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select

from ..database import session_scope, utcnow
from ..errors import AppError
from ..integrations.leetcode import fetch_public, parse_username
from ..models import LeetCodeSnapshot, ProfileClaim, ProfileDocument
from ..services import job_queue
from ..services.documents import ExtractedDoc, extract_text
from ..services.events import audit
from ..services.profile import claim_audit, ingest_profile, resume_bullets
from .deps import user_id

router = APIRouter(prefix="/api/profile", tags=["profile"])


def _reassess(uid: str) -> None:
    job_queue.enqueue("reassess_jobs", {"user_id": uid, "trigger": "profile_update"}, f"reassess:{uid}:{utcnow()}", user_id=uid)


@router.post("/upload")
async def upload(kind: str = Form(...), consent: bool = Form(...), file: UploadFile = File(...), uid: str = Depends(user_id)) -> dict[str, Any]:
    if not consent:
        raise AppError("consent_required", "Consent is required to store and parse this document.", status=428)
    data = await file.read()
    doc = extract_text(file.filename or "profile", file.content_type, data)
    res = await ingest_profile(uid, kind, doc, file.filename, file.content_type, len(data))
    _reassess(uid)
    return res


class PasteProfile(BaseModel):
    text: str = Field(min_length=30, max_length=100_000)
    kind: str = "pasted_profile"
    consent: bool


@router.post("/paste")
async def paste(body: PasteProfile, uid: str = Depends(user_id)) -> dict[str, Any]:
    if not body.consent:
        raise AppError("consent_required", "Consent is required to store and parse this text.", status=428)
    from ..integrations.safe_fetch import normalize_text

    res = await ingest_profile(uid, body.kind, ExtractedDoc(normalize_text(body.text), [0], "pasted"), None, "text/plain", len(body.text))
    _reassess(uid)
    return res


@router.get("/documents")
def documents(uid: str = Depends(user_id)) -> list[dict[str, Any]]:
    with session_scope() as db:
        rows = db.execute(select(ProfileDocument).where(ProfileDocument.user_id == uid).order_by(ProfileDocument.created_at.desc())).scalars()
        out = []
        for d in rows:
            n = db.query(ProfileClaim).filter(ProfileClaim.document_id == d.id).count()
            out.append({"id": d.id, "kind": d.kind, "filename": d.filename, "size": d.size, "active": d.active, "parse_method": d.parse_method,
                        "parse_warning": d.parse_warning, "structured": d.structured, "claims": n, "created_at": d.created_at, "pages": len(d.page_offsets or [])})
        return out


@router.get("/documents/{doc_id}/text")
def document_text(doc_id: str, uid: str = Depends(user_id)) -> dict[str, Any]:
    with session_scope() as db:
        d = db.get(ProfileDocument, doc_id)
        if d is None or d.user_id != uid:
            raise AppError("not_found", "Document not found.", status=404)
        return {"text": d.extracted_text, "page_offsets": d.page_offsets}


@router.delete("/documents/{doc_id}")
def delete_document(doc_id: str, uid: str = Depends(user_id)) -> dict[str, Any]:
    with session_scope() as db:
        d = db.get(ProfileDocument, doc_id)
        if d is None or d.user_id != uid:
            raise AppError("not_found", "Document not found.", status=404)
        db.delete(d)
        audit("profile_deleted", {"document_id": doc_id}, uid, db=db)
    _reassess(uid)
    return {"deleted": True}


@router.get("/claims")
def claims(uid: str = Depends(user_id)) -> list[dict[str, Any]]:
    return claim_audit(uid)


@router.post("/resume-bullets")
async def bullets(uid: str = Depends(user_id)) -> dict[str, Any]:
    return await resume_bullets(uid)


# ------------------------------------------------------------------ LeetCode

lc = APIRouter(prefix="/api/leetcode", tags=["leetcode"])


class LCFetch(BaseModel):
    username_or_url: str = Field(min_length=1, max_length=200)


@lc.post("/fetch")
async def lc_fetch(body: LCFetch, uid: str = Depends(user_id)) -> dict[str, Any]:
    username = parse_username(body.username_or_url)
    try:
        res = await fetch_public(username)
        status = "ok" if all(v == "ok" for v in res["field_status"].values()) else "partial"
        err = None
    except AppError as e:
        with session_scope() as db:
            db.add(LeetCodeSnapshot(user_id=uid, username=username, source_url=f"https://leetcode.com/u/{username}/", provider="leetcode_public_graphql",
                                    fields={}, field_status=e.detail if isinstance(e.detail, dict) else {}, status="error", error=e.message))
        raise
    with session_scope() as db:
        s = LeetCodeSnapshot(user_id=uid, username=username, source_url=f"https://leetcode.com/u/{username}/", provider="leetcode_public_graphql",
                             fields=res["fields"], field_status={**res["field_status"], "_query_version": res["query_version"]}, status=status, error=err)
        db.add(s)
        db.flush()
        sid = s.id
    _reassess(uid)
    return lc_latest(uid) | {"snapshot_id": sid}


class LCManual(BaseModel):
    username: str = Field(min_length=1, max_length=60)
    easy: int = Field(ge=0, le=5000)
    medium: int = Field(ge=0, le=5000)
    hard: int = Field(ge=0, le=5000)
    contest_rating: float | None = Field(default=None, ge=0, le=5000)
    tag_counts: dict[str, int] = Field(default_factory=dict)
    note: str | None = Field(default=None, max_length=300)


@lc.post("/manual")
def lc_manual(body: LCManual, uid: str = Depends(user_id)) -> dict[str, Any]:
    fields = {"solved": {"Easy": body.easy, "Medium": body.medium, "Hard": body.hard, "All": body.easy + body.medium + body.hard},
              "tag_counts": {k.strip().lower().replace(" ", "-"): int(v) for k, v in body.tag_counts.items() if k.strip()},
              "contest": {"rating": body.contest_rating} if body.contest_rating else None, "note": body.note}
    with session_scope() as db:
        db.add(LeetCodeSnapshot(user_id=uid, username=body.username, source_url=f"https://leetcode.com/u/{body.username}/", provider="user_supplied",
                                fields=fields, field_status={"all": "user_supplied (not verified)"}, status="user_supplied"))
    _reassess(uid)
    return lc_latest(uid)


@lc.get("/latest")
def lc_latest(uid: str = Depends(user_id)) -> dict[str, Any]:
    with session_scope() as db:
        rows = db.execute(select(LeetCodeSnapshot).where(LeetCodeSnapshot.user_id == uid).order_by(LeetCodeSnapshot.retrieved_at.desc()).limit(10)).scalars().all()
        snaps = [{"id": s.id, "username": s.username, "source_url": s.source_url, "provider": s.provider, "fields": s.fields, "field_status": s.field_status,
                  "status": s.status, "error": s.error, "retrieved_at": s.retrieved_at} for s in rows]
    latest = next((s for s in snaps if s["status"] != "error"), None)
    return {"latest": latest, "history": snaps,
            "note": "LeetCode is a PROBLEM_SOLVING_SIGNAL only. It never proves API, SQL, testing or deployment skills."}
