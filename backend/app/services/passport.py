"""Verified Skill Passport: Ed25519-signed, revocable, user-redacted evidence artifact.

The signature proves the artifact was issued by this installation and not modified. It does
not prove job performance. Keys live in data/keys; old public keys are kept for verification
after rotation (key id = first 16 hex chars of SHA-256 of the raw public key).
"""
from __future__ import annotations

import base64
import json
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from sqlalchemy import select

from ..analyzers.taxonomy import skill_name
from ..config import get_settings
from ..database import session_scope, utcnow
from ..errors import AppError
from ..models import (Assessment, AssessmentRow, AssessmentRowEvidence, EvidenceRecord, JobPosting, PassportEvidence, Repository, Requirement,
                      SkillPassport, User)
from .events import audit


def _keys_dir() -> Path:
    return Path(get_settings().keys_dir)


def _kid(pub: Ed25519PublicKey) -> str:
    import hashlib

    raw = pub.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return hashlib.sha256(raw).hexdigest()[:16]


def _registry_path() -> Path:
    return _keys_dir() / "passport_public_keys.json"


def active_key() -> tuple[Ed25519PrivateKey, str]:
    p = _keys_dir() / "passport_ed25519_active.pem"
    if p.exists():
        priv = serialization.load_pem_private_key(p.read_bytes(), password=None)
    else:
        priv = Ed25519PrivateKey.generate()
        p.write_bytes(priv.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    pub = priv.public_key()
    kid = _kid(pub)
    reg = json.loads(_registry_path().read_text()) if _registry_path().exists() else {}
    if kid not in reg:
        reg[kid] = {"public_key": base64.b64encode(pub.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode(),
                    "alg": "Ed25519", "created_at": utcnow()}
        _registry_path().write_text(json.dumps(reg, indent=2))
    return priv, kid  # type: ignore[return-value]


def public_keys() -> dict[str, Any]:
    active_key()
    return json.loads(_registry_path().read_text())


def rotate_key() -> str:
    p = _keys_dir() / "passport_ed25519_active.pem"
    if p.exists():
        p.rename(_keys_dir() / f"passport_ed25519_retired_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}.pem")
    return active_key()[1]


def canonical(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def create_passport(user_id: str, assessment_id: str, row_ids: list[str], *, title: str, visibility: str, include_permalinks: bool,
                    include_display_name: bool, expires_days: int | None) -> dict[str, Any]:
    if visibility not in ("public", "private"):
        raise AppError("bad_visibility", "visibility must be public or private", status=422)
    s = get_settings()
    with session_scope() as db:
        a = db.get(Assessment, assessment_id)
        if a is None or a.user_id != user_id:
            raise AppError("not_found", "Assessment not found.", status=404)
        jp = db.get(JobPosting, a.job_posting_id)
        rows = db.execute(select(AssessmentRow).where(AssessmentRow.assessment_id == assessment_id, AssessmentRow.id.in_(row_ids))).scalars().all()
        if not rows:
            raise AppError("no_rows", "Select at least one skill to include.", status=422)
        skills = []
        ev_ids: list[str] = []
        for r in rows:
            req = db.get(Requirement, r.requirement_id)
            evs = db.execute(select(EvidenceRecord).join(AssessmentRowEvidence, AssessmentRowEvidence.evidence_id == EvidenceRecord.id)
                             .where(AssessmentRowEvidence.row_id == r.id)).scalars().all()
            items = []
            for e in evs:
                repo = db.get(Repository, e.repository_id)
                show_link = include_permalinks and (not repo.private)
                items.append({"source_type": e.source_type, "repo": e.repo_full_name if show_link or not repo.private else "private repository",
                              "commit_sha": e.commit_sha, "path": e.path if show_link else None, "lines": f"{e.start_line}-{e.end_line}" if show_link else None,
                              "permalink": e.permalink if show_link else None, "detector": f"{e.detector}@{e.detector_version}", "depth": e.depth,
                              "snippet_sha256": e.snippet_digest})
                ev_ids.append(e.id)
            sig = r.signals or {}
            skills.append({"skill_key": r.skill_key, "skill": skill_name(r.skill_key), "requirement_quote": req.quote, "verdict": r.verdict,
                           "verification_date": r.evaluated_at, "evidence": items,
                           "runtime_test_result": sig.get("runtime"), "understanding": sig.get("understanding")})
        user = db.get(User, user_id)
        now = datetime.now(timezone.utc)
        expires = now + timedelta(days=expires_days or s.passport_validity_days)
        artifact_id = "pp_" + secrets.token_urlsafe(12)
        payload = {
            "type": "CareerCoachSkillPassport", "version": 1, "artifact_id": artifact_id, "issued_at": now.isoformat(), "expires_at": expires.isoformat(),
            "issuer": {"name": "AI Career Coach (self-hosted)", "base_url": s.app_public_url or s.app_base_url},
            "subject": {"display_name": user.display_name} if include_display_name and user.display_name else {"display_name": None},
            "job": {"title": jp.title, "company": jp.company, "source_url": jp.source_url},
            "assessment": {"id": a.id, "version": a.version, "created_at": a.created_at, "scopes": [{"repo": sc["repo"] if include_permalinks else None,
                                                                                                    "branch": sc["branch"], "commit_sha": sc["commit_sha"]} for sc in a.scopes or []]},
            "skills": skills,
            "scope_statement": "Verdicts describe evidence found in the listed repositories at the listed commits only. Absence of evidence is not absence of skill.",
            "limitations": "Static analysis + optional sandbox runs. The signature proves issuance by this system and integrity of this document; "
                           "it does not prove job performance or guarantee any hiring outcome.",
        }
        priv, kid = active_key()
        sig = base64.urlsafe_b64encode(priv.sign(canonical(payload))).decode()
        import hashlib

        p = SkillPassport(artifact_id=artifact_id, user_id=user_id, title=title[:300], visibility=visibility, payload=payload,
                          canonical_hash=hashlib.sha256(canonical(payload)).hexdigest(), signature=sig, key_id=kid, expires_at=expires.isoformat())
        db.add(p)
        db.flush()
        for eid in dict.fromkeys(ev_ids):
            db.add(PassportEvidence(passport_id=p.id, evidence_id=eid))
        audit("passport_created", {"artifact_id": artifact_id, "visibility": visibility, "skills": len(skills)}, user_id, db=db)
        return {"artifact_id": artifact_id, "id": p.id}


def verify(artifact_id: str, payload_override: dict | None = None, signature_override: str | None = None) -> dict[str, Any]:
    with session_scope() as db:
        p = db.execute(select(SkillPassport).where(SkillPassport.artifact_id == artifact_id)).scalar_one_or_none()
        if p is None:
            raise AppError("not_found", "Passport not found.", status=404)
        payload = payload_override or p.payload
        signature = signature_override or p.signature
        kid, revoked, reason, expires = p.key_id, p.revoked_at, p.revoke_reason, p.expires_at
    keys = public_keys()
    if kid not in keys:
        return {"valid": False, "reason": "unknown signing key"}
    pub = Ed25519PublicKey.from_public_bytes(base64.b64decode(keys[kid]["public_key"]))
    try:
        pub.verify(base64.urlsafe_b64decode(signature), canonical(payload))
        sig_ok = True
    except (InvalidSignature, ValueError):
        sig_ok = False
    expired = datetime.fromisoformat(expires) < datetime.now(timezone.utc)
    return {"valid": sig_ok and not revoked and not expired, "signature_valid": sig_ok, "revoked": bool(revoked), "revoked_at": revoked,
            "revoke_reason": reason, "expired": expired, "expires_at": expires, "key_id": kid, "alg": "Ed25519"}


def revoke(user_id: str, artifact_id: str, reason: str) -> None:
    with session_scope() as db:
        p = db.execute(select(SkillPassport).where(SkillPassport.artifact_id == artifact_id)).scalar_one_or_none()
        if p is None or p.user_id != user_id:
            raise AppError("not_found", "Passport not found.", status=404)
        p.revoked_at = utcnow()
        p.revoke_reason = reason[:500] or "revoked by owner"
        audit("passport_revoked", {"artifact_id": artifact_id}, user_id, db=db)
