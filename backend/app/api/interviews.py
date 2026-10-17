"""Mock interviews and Code-Defense sessions."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, Form, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select

from ..database import session_scope
from ..errors import AppError
from ..integrations.transcription import transcription_status
from ..models import InterviewAnswer, InterviewEvaluation, InterviewSession, JobPosting, MediaFile
from ..services import interviews as svc
from .deps import user_id

router = APIRouter(prefix="/api/interviews", tags=["interviews"])


def _answer_dict(a: InterviewAnswer, ev: InterviewEvaluation | None, media: MediaFile | None) -> dict[str, Any]:
    return {"id": a.id, "answer_source": a.answer_source, "transcript_raw": a.transcript_raw, "transcript_final": a.transcript_final,
            "transcript_edited": a.transcript_edited, "transcription_provider": a.transcription_provider, "transcription_status": a.transcription_status,
            "transcription_error": a.transcription_error, "transcribed_at": a.transcribed_at, "duration_seconds": a.duration_seconds, "timed_out": a.timed_out,
            "status": a.status, "error": a.error, "created_at": a.created_at,
            "media": {"id": media.id, "size": media.size, "mime": media.mime, "retention_status": media.retention_status} if media else None,
            "evaluation": {"id": ev.id, "scores": ev.scores, "overall": ev.overall, "strengths": ev.strengths, "missing_points": ev.missing_points,
                           "correction": ev.correction, "model_answer": ev.model_answer, "delivery_metrics": ev.delivery_metrics,
                           "followup_suggestion": ev.followup_suggestion, "provider": ev.provider, "model": ev.model,
                           "evaluator_version": ev.evaluator_version, "created_at": ev.created_at} if ev else None}


def session_dict(uid: str, session_id: str) -> dict[str, Any]:
    with session_scope() as db:
        s = db.get(InterviewSession, session_id)
        if s is None or s.user_id != uid:
            raise AppError("not_found", "Interview session not found.", status=404)
        jp = db.get(JobPosting, s.job_posting_id) if s.job_posting_id else None
        qs = svc.ordered_questions(db, session_id)
        out_q = []
        for q in qs:
            a = db.execute(select(InterviewAnswer).where(InterviewAnswer.question_id == q.id)).scalar_one_or_none()
            ev = db.execute(select(InterviewEvaluation).where(InterviewEvaluation.answer_id == a.id)).scalar_one_or_none() if a else None
            media = db.get(MediaFile, a.media_id) if a and a.media_id else None
            out_q.append({"id": q.id, "order_index": q.order_index, "qtype": q.qtype, "prompt": q.prompt, "target_skill_key": q.target_skill_key,
                          "requirement_id": q.requirement_id, "evidence_id": q.evidence_id, "assessment_row_id": q.assessment_row_id, "provenance": q.provenance,
                          "difficulty": q.difficulty, "prep_seconds": q.prep_seconds, "answer_seconds": q.answer_seconds, "rubric": {k: v for k, v in (q.rubric or {}).items() if k != "key_point_keywords"},
                          "max_followups": q.max_followups, "parent_question_id": q.parent_question_id, "followup_depth": q.followup_depth,
                          "code_context": q.code_context, "challenge": {k: v for k, v in (q.challenge or {}).items() if k != "bug_description"} if q.challenge else None,
                          "status": q.status, "answer": _answer_dict(a, ev, media) if a else None})
        return {"id": s.id, "kind": s.kind, "status": s.status, "plan_version": s.plan_version, "plan_generator": s.plan_generator,
                "plan_frozen_at": s.plan_frozen_at, "consent": s.consent, "current_index": s.current_index, "settings": s.settings,
                "started_at": s.started_at, "ended_at": s.ended_at, "report": s.report, "created_at": s.created_at,
                "job": {"id": jp.id, "title": jp.title, "company": jp.company, "source_url": jp.source_url} if jp else None, "questions": out_q}


class CreateBody(BaseModel):
    job_posting_id: str
    n_questions: int = Field(default=7, ge=3, le=12)


@router.post("")
async def create(body: CreateBody, uid: str = Depends(user_id)) -> dict[str, Any]:
    sid = await svc.create_session(uid, body.job_posting_id, body.n_questions)
    return session_dict(uid, sid)


class DefenseBody(BaseModel):
    evidence_ids: list[str] = Field(min_length=1, max_length=5)
    include_bug_challenge: bool = False
    job_posting_id: str | None = None


@router.post("/code-defense")
async def create_defense(body: DefenseBody, uid: str = Depends(user_id)) -> dict[str, Any]:
    sid = await svc.create_code_defense(uid, body.evidence_ids, body.include_bug_challenge, body.job_posting_id)
    return session_dict(uid, sid)


@router.get("")
def list_sessions(kind: str | None = None, uid: str = Depends(user_id)) -> list[dict[str, Any]]:
    with session_scope() as db:
        q = select(InterviewSession).where(InterviewSession.user_id == uid)
        if kind:
            q = q.where(InterviewSession.kind == kind)
        rows = db.execute(q.order_by(InterviewSession.created_at.desc()).limit(100)).scalars().all()
        out = []
        for s in rows:
            jp = db.get(JobPosting, s.job_posting_id) if s.job_posting_id else None
            out.append({"id": s.id, "kind": s.kind, "status": s.status, "plan_generator": s.plan_generator, "created_at": s.created_at,
                        "started_at": s.started_at, "ended_at": s.ended_at, "overall": (s.report or {}).get("overall"),
                        "job": {"id": jp.id, "title": jp.title, "company": jp.company} if jp else None})
        return out


@router.get("/capabilities")
def capabilities(uid: str = Depends(user_id)) -> dict[str, Any]:
    from ..integrations.llm import providers_for

    p = providers_for(uid)
    return {"transcription": transcription_status(uid), "llm": {"configured": bool(p), "model": p[0].model if p else None, "local": p[0].is_local if p else None}}


@router.get("/{session_id}")
def get_session(session_id: str, uid: str = Depends(user_id)) -> dict[str, Any]:
    return session_dict(uid, session_id)


class StartBody(BaseModel):
    camera: bool = False
    microphone: bool = False
    audio_upload: bool = False
    retain_audio: bool = False


@router.post("/{session_id}/start")
def start(session_id: str, body: StartBody, uid: str = Depends(user_id)) -> dict[str, Any]:
    svc.start_session(uid, session_id, body.model_dump())
    return session_dict(uid, session_id)


@router.post("/{session_id}/questions/{question_id}/answer")
async def answer(session_id: str, question_id: str, typed_text: str | None = Form(default=None), duration_seconds: float | None = Form(default=None),
                 timed_out: bool = Form(default=False), skipped: bool = Form(default=False), audio: UploadFile | None = File(default=None),
                 uid: str = Depends(user_id)) -> dict[str, Any]:
    data = await audio.read() if audio is not None else None
    aid = svc.submit_answer(uid, session_id, question_id, typed_text=typed_text, audio=data or None, audio_mime=audio.content_type if audio else None,
                            duration_seconds=duration_seconds, timed_out=timed_out, skipped=skipped)
    return {"answer_id": aid, "session": session_dict(uid, session_id)}


class TranscriptEdit(BaseModel):
    text: str = Field(max_length=30000)


@router.patch("/answers/{answer_id}/transcript")
def edit(answer_id: str, body: TranscriptEdit, uid: str = Depends(user_id)) -> dict[str, Any]:
    svc.edit_transcript(uid, answer_id, body.text)
    return {"ok": True}


@router.post("/answers/{answer_id}/evaluate")
def evaluate(answer_id: str, uid: str = Depends(user_id)) -> dict[str, Any]:
    svc.request_evaluation(uid, answer_id)
    return {"queued": True}


@router.post("/{session_id}/advance")
def advance(session_id: str, uid: str = Depends(user_id)) -> dict[str, Any]:
    return svc.advance(uid, session_id)


@router.post("/{session_id}/complete")
def complete(session_id: str, uid: str = Depends(user_id)) -> dict[str, Any]:
    return svc.complete(uid, session_id)


@router.get("/{session_id}/report")
def report(session_id: str, uid: str = Depends(user_id)) -> dict[str, Any]:
    return svc.build_report(uid, session_id)


@router.delete("/{session_id}/media")
def delete_media(session_id: str, uid: str = Depends(user_id)) -> dict[str, Any]:
    return {"deleted": svc.delete_session_media(uid, session_id)}


@router.delete("/{session_id}")
def delete(session_id: str, uid: str = Depends(user_id)) -> dict[str, Any]:
    svc.delete_session(uid, session_id)
    return {"deleted": True}
