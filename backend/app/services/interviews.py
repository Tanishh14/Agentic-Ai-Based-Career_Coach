"""Interview sessions (mock + code-defense): plan, server-side state, answers, transcription, evaluation, report."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from sqlalchemy import select

from ..agents.code_defense import llm_bug_challenge, llm_question, template_question, validate_citation
from ..agents.interview_architect import PLAN_SYSTEM, InterviewPlanLLM, PlanContext, llm_prompt, rubric_for, template_plan, validate_llm_plan
from ..agents.interview_evaluator import EVALUATOR_VERSION, delivery_metrics, heuristic_evaluate, llm_evaluate
from ..agents.orchestrator import AgentRun
from ..database import session_scope, utcnow
from ..errors import AppError, NotConfigured
from ..integrations.llm import LLMFailed, complete_json, llm_available, providers_for
from ..models import (Assessment, AssessmentRow, AssessmentRowEvidence, EvidenceRecord, InterviewAnswer, InterviewEvaluation,
                      InterviewQuestion, InterviewSession, JobPosting, LeetCodeSnapshot, MediaFile, ProfileClaim, ProfileDocument,
                      Requirement, UnderstandingEvidence)
from . import job_queue
from .assessments import requirement_dict
from .events import audit, emit
from .integrations_store import user_settings
from .storage import delete_media, path_for, save_media


def _code_allowed(user_id: str) -> bool:
    with session_scope() as db:
        allow = user_settings(db, user_id).get("allow_code_snippets_to_llm")
    provs = providers_for(user_id)
    return bool(allow) or (bool(provs) and provs[0].is_local)


def _plan_context(user_id: str, job_posting_id: str, n: int) -> tuple[PlanContext, str | None]:
    with session_scope() as db:
        jp = db.get(JobPosting, job_posting_id)
        if jp is None or jp.user_id != user_id:
            raise AppError("not_found", "Job posting not found.", status=404)
        if jp.status != "analyzed":
            raise AppError("job_not_analyzed", "Analyse this job first so the interview can use its requirements.", status=409)
        reqs = [requirement_dict(r) for r in db.execute(select(Requirement).where(Requirement.posting_id == job_posting_id)).scalars()]
        a = db.execute(select(Assessment).where(Assessment.job_posting_id == job_posting_id).order_by(Assessment.version.desc()).limit(1)).scalar_one_or_none()
        rows: list[dict] = []
        evidence: list[dict] = []
        if a:
            ars = db.execute(select(AssessmentRow).where(AssessmentRow.assessment_id == a.id)).scalars().all()
            rows = [{"id": r.id, "requirement_id": r.requirement_id, "skill_key": r.skill_key, "verdict": r.verdict} for r in ars]
            ev_ids = [eid for (eid,) in db.execute(select(AssessmentRowEvidence.evidence_id).where(AssessmentRowEvidence.row_id.in_([r.id for r in ars])))]
            evs = db.execute(select(EvidenceRecord).where(EvidenceRecord.id.in_(ev_ids), EvidenceRecord.verification_status == "accepted",
                                                          EvidenceRecord.source_type.in_(("CODE_IMPLEMENTATION", "CODE_TEST")))).scalars().all()
            evs = sorted(evs, key=lambda e: (-{"substantial": 3, "intermediate": 2, "basic": 1}.get(e.depth, 0), -e.confidence))
            seen_paths = set()
            for e in evs:
                if e.path in seen_paths:
                    continue
                seen_paths.add(e.path)
                evidence.append({"id": e.id, "skill_key": e.skill_key, "repo": e.repo_full_name, "path": e.path, "start_line": e.start_line,
                                 "end_line": e.end_line, "reason": e.reason, "snippet": e.snippet[:2000], "permalink": e.permalink,
                                 "commit_sha": e.commit_sha, "node_type": e.node_type})
                if len(evidence) >= 6:
                    break
        claims = [{"id": c.id, "text": c.text, "claim_type": c.claim_type, "evidence_class": c.evidence_class}
                  for c, _ in db.execute(select(ProfileClaim, ProfileDocument).join(ProfileDocument, ProfileClaim.document_id == ProfileDocument.id)
                                         .where(ProfileDocument.user_id == user_id, ProfileDocument.active.is_(True))).all()][:30]
        lc = db.execute(select(LeetCodeSnapshot).where(LeetCodeSnapshot.user_id == user_id).order_by(LeetCodeSnapshot.retrieved_at.desc()).limit(1)).scalar_one_or_none()
        job = {"title": jp.title, "company": jp.company, "source_url": jp.source_url}
        ctx = PlanContext(job, reqs, rows, evidence, claims, lc.fields if lc else None, _code_allowed(user_id), n)
        return ctx, a.id if a else None


async def create_session(user_id: str, job_posting_id: str, n_questions: int = 7) -> str:
    n_questions = max(3, min(12, n_questions))
    ctx, assessment_id = _plan_context(user_id, job_posting_id, n_questions)
    run = AgentRun(user_id, "interview_plan")
    generator = "template generator (no LLM configured): questions built from the real JD, gap matrix and evidence"
    planned = []
    notes: list[str] = []
    try:
        if llm_available(user_id):
            try:
                run.charge_llm()
                res = await run.tool("generate_interview_plan", f"{n_questions} questions for job {job_posting_id[:8]}", complete_json, user_id,
                                     PLAN_SYSTEM, llm_prompt(ctx), InterviewPlanLLM, temperature=0.5, max_tokens=4000,
                                     summarize=lambda r: f"{len(r.data.questions)} proposed by {r.model}")
                planned, rejected = validate_llm_plan(ctx, res.data)
                generator = f"llm:{res.provider}:{res.model} (validated: {len(planned)} kept, {len(rejected)} rejected)"
                if rejected:
                    notes.append(f"Rejected LLM questions: {'; '.join(rejected[:5])}")
            except (LLMFailed, NotConfigured) as e:
                notes.append(f"LLM plan failed ({e.message}); used template generator.")
        if len(planned) < min(n_questions, 4):
            extra = template_plan(ctx)
            from ..agents.interview_architect import _dup

            for q in extra:
                if len(planned) >= n_questions:
                    break
                if all(not _dup(q.prompt, p.prompt) for p in planned):
                    planned.append(q)
            if generator.startswith("llm"):
                generator += " + template top-up"
        if not planned:
            raise AppError("plan_empty", "Could not build any grounded question. Make sure the job has requirements and run an assessment.", status=422)
        run.record("verify", result_summary=f"plan validated: {len(planned)} question(s), types: {', '.join(sorted({q.qtype for q in planned}))}")
        with session_scope() as db:
            s = InterviewSession(user_id=user_id, kind="mock", job_posting_id=job_posting_id, assessment_id=assessment_id, plan_version=1,
                                 plan_generator=generator, status="planned", settings={"notes": notes, "n_questions": n_questions})
            db.add(s)
            db.flush()
            for i, q in enumerate(planned):
                db.add(InterviewQuestion(session_id=s.id, order_index=float(i), qtype=q.qtype, prompt=q.prompt, target_skill_key=q.target_skill_key,
                                         requirement_id=q.requirement_id, evidence_id=q.evidence_id, assessment_row_id=q.assessment_row_id,
                                         provenance=q.provenance, difficulty=q.difficulty, prep_seconds=q.prep_seconds, answer_seconds=q.answer_seconds,
                                         rubric=q.rubric, max_followups=q.max_followups, code_context=q.code_context))
            sid = s.id
        run.finish("succeeded", f"interview plan {sid[:8]} with {len(planned)} questions ({generator})")
        return sid
    except Exception as e:
        run.finish("failed", error=getattr(e, "message", str(e)))
        raise


async def create_code_defense(user_id: str, evidence_ids: list[str], include_bug_challenge: bool = False, job_posting_id: str | None = None) -> str:
    if not evidence_ids:
        raise AppError("no_evidence", "Select at least one evidence item to defend.", status=422)
    run = AgentRun(user_id, "code_defense_plan")
    questions: list[dict[str, Any]] = []
    gen = set()
    try:
        for eid in evidence_ids[:5]:
            cc = await run.tool("read_file_at_commit", f"validate citation {eid[:8]}", validate_citation, user_id, eid,
                                summarize=lambda d: f"{d['repo']}:{d['path']} L{d['start_line']}-{d['end_line']} digest verified")
            prompt, key_points = template_question(cc)
            generator = "template"
            if llm_available(user_id) and _code_allowed(user_id):
                try:
                    run.charge_llm()
                    q, prov, model = await run.tool("generate_question_from_code", f"{cc['path']}", llm_question, user_id, cc,
                                                    summarize=lambda r: f"{r[0].focus} question by {r[2]}")
                    prompt, key_points, generator = q.prompt, q.key_points, f"llm:{model}"
                except (LLMFailed, NotConfigured) as e:
                    run.record("plan", result_summary=f"LLM question failed ({e.message}); template used", status="degraded")
            gen.add(generator)
            rub = rubric_for(cc["skill_key"], "project_deep_dive")
            rub["key_points"] = key_points
            if generator != "template":
                rub["key_point_keywords"] = {}
            questions.append({"qtype": "code_defense", "prompt": prompt, "skill": cc["skill_key"], "evidence_id": eid, "rubric": rub, "cc": cc,
                              "provenance": {"kind": "evidence", "id": eid, "permalink": cc["permalink"], "commit_sha": cc["commit_sha"]}})
            if include_bug_challenge and llm_available(user_id) and _code_allowed(user_id) and len([q for q in questions if q["qtype"] == "debugging_challenge"]) < 1:
                try:
                    run.charge_llm()
                    ch, model = await run.tool("generate_question_from_code", f"bug challenge from {cc['path']}", llm_bug_challenge, user_id, cc,
                                               summarize=lambda r: "hypothetical diff generated")
                    rub2 = rubric_for(cc["skill_key"], "debugging")
                    rub2["key_points"] = ch["key_points"]
                    rub2["key_point_keywords"] = {}
                    questions.append({"qtype": "debugging_challenge", "skill": cc["skill_key"], "evidence_id": eid, "rubric": rub2, "cc": cc,
                                      "prompt": f"Bug-diagnosis challenge (hypothetical). Users report: {ch['symptom']} Study the diff below against your "
                                                f"own code in {cc['path']} and explain the root cause, how you would confirm it, and the fix.",
                                      "challenge": ch, "provenance": {"kind": "evidence", "id": eid, "permalink": cc["permalink"], "hypothetical": True}})
                except Exception as e:  # noqa: BLE001
                    run.record("plan", result_summary=f"bug challenge skipped: {getattr(e, 'message', str(e))[:200]}", status="degraded")
        with session_scope() as db:
            s = InterviewSession(user_id=user_id, kind="code_defense", job_posting_id=job_posting_id, plan_version=1,
                                 plan_generator=", ".join(sorted(gen)), status="planned", settings={"evidence_ids": evidence_ids})
            db.add(s)
            db.flush()
            for i, q in enumerate(questions):
                db.add(InterviewQuestion(session_id=s.id, order_index=float(i), qtype=q["qtype"], prompt=q["prompt"], target_skill_key=q["skill"],
                                         evidence_id=q["evidence_id"], provenance=q["provenance"], difficulty="medium", prep_seconds=45,
                                         answer_seconds=240 if q["qtype"] == "debugging_challenge" else 180, rubric=q["rubric"], max_followups=2,
                                         code_context={k: q["cc"][k] for k in ("repo", "path", "commit_sha", "start_line", "end_line", "permalink", "snippet", "evidence_id", "skill_key")},
                                         challenge=q.get("challenge")))
            sid = s.id
        run.finish("succeeded", f"code-defense session {sid[:8]} with {len(questions)} question(s)")
        return sid
    except Exception as e:
        run.finish("failed", error=getattr(e, "message", str(e)))
        raise


# ------------------------------------------------------------------ state machine


def _session(db, user_id: str, session_id: str) -> InterviewSession:
    s = db.get(InterviewSession, session_id)
    if s is None or s.user_id != user_id:
        raise AppError("not_found", "Interview session not found.", status=404)
    return s


def ordered_questions(db, session_id: str) -> list[InterviewQuestion]:
    return list(db.execute(select(InterviewQuestion).where(InterviewQuestion.session_id == session_id).order_by(InterviewQuestion.order_index)).scalars())


def start_session(user_id: str, session_id: str, consent: dict[str, Any]) -> None:
    with session_scope() as db:
        s = _session(db, user_id, session_id)
        if s.status not in ("planned", "active"):
            raise AppError("bad_state", f"Session is {s.status}.", status=409)
        s.consent = {"camera": bool(consent.get("camera")), "microphone": bool(consent.get("microphone")),
                     "audio_upload": bool(consent.get("audio_upload")), "retain_audio": bool(consent.get("retain_audio")), "at": utcnow()}
        if s.status == "planned":
            s.status = "active"
            s.started_at = utcnow()
            s.plan_frozen_at = utcnow()
        audit("interview_consent", {"session_id": session_id, **s.consent}, user_id, db=db)


def submit_answer(user_id: str, session_id: str, question_id: str, *, typed_text: str | None, audio: bytes | None, audio_mime: str | None,
                  duration_seconds: float | None, timed_out: bool, skipped: bool) -> str:
    media_id = None
    with session_scope() as db:
        s = _session(db, user_id, session_id)
        if s.status != "active":
            raise AppError("bad_state", "Start the session before answering.", status=409)
        q = db.get(InterviewQuestion, question_id)
        if q is None or q.session_id != session_id:
            raise AppError("not_found", "Question not found.", status=404)
        if db.execute(select(InterviewAnswer.id).where(InterviewAnswer.question_id == question_id)).first():
            raise AppError("already_answered", "This question already has an answer.", status=409)
        consent = s.consent or {}
    if audio and not skipped:
        if not consent.get("audio_upload"):
            raise AppError("consent_required", "Audio upload/transcription consent was not given for this session.", status=403,
                           action="Type your answer, or restart with transcription consent.")
        media_id = save_media(user_id, audio, audio_mime or "audio/webm", "interview_answer").id
    with session_scope() as db:
        q = db.get(InterviewQuestion, question_id)
        if skipped:
            source, status, tstatus = "none", "evaluated", "not_applicable"
            q.status = "skipped"
        elif media_id:
            source, status, tstatus = "audio", "transcribing", "pending"
            q.status = "timed_out" if timed_out else "answered"
        else:
            if not (typed_text or "").strip() and not timed_out:
                raise AppError("empty_answer", "Provide a typed answer, a recording, or skip.", status=422)
            source, status, tstatus = "typed", "review", "not_applicable"
            q.status = "timed_out" if timed_out else "answered"
        a = InterviewAnswer(session_id=session_id, question_id=question_id, answer_source=source, transcript_raw=typed_text if source == "typed" else None,
                            transcript_final=typed_text if source == "typed" else None, transcription_status=tstatus, duration_seconds=duration_seconds,
                            timed_out=timed_out, media_id=media_id, status=status)
        if source == "typed" and not (typed_text or "").strip():
            a.transcript_final = ""
            a.error = "Timed out with no answer recorded."
        db.add(a)
        db.flush()
        aid = a.id
    if media_id:
        job_queue.enqueue("transcribe_answer", {"answer_id": aid}, f"transcribe:{aid}", user_id=user_id, max_attempts=3)
    emit("interview_answer", {"session_id": session_id, "answer_id": aid, "status": status}, user_id)
    return aid


def edit_transcript(user_id: str, answer_id: str, text: str) -> None:
    with session_scope() as db:
        a = db.get(InterviewAnswer, answer_id)
        if a is None:
            raise AppError("not_found", "Answer not found.", status=404)
        _session(db, user_id, a.session_id)
        if a.status not in ("review", "evaluated", "failed"):
            raise AppError("bad_state", f"Transcript cannot be edited while {a.status}.", status=409)
        a.transcript_final = text
        a.transcript_edited = True
        a.status = "review"


def request_evaluation(user_id: str, answer_id: str) -> None:
    with session_scope() as db:
        a = db.get(InterviewAnswer, answer_id)
        if a is None:
            raise AppError("not_found", "Answer not found.", status=404)
        _session(db, user_id, a.session_id)
        if a.status not in ("review", "failed", "evaluated"):
            raise AppError("bad_state", f"Answer is {a.status}; review the transcript first.", status=409)
        a.status = "evaluating"
        old = db.execute(select(InterviewEvaluation).where(InterviewEvaluation.answer_id == answer_id)).scalar_one_or_none()
        if old:
            db.delete(old)
    job_queue.enqueue("evaluate_answer", {"answer_id": answer_id}, f"evaluate:{answer_id}:{utcnow()}", user_id=user_id, max_attempts=3)


async def transcribe_answer_job(answer_id: str) -> dict[str, Any]:
    from ..integrations.transcription import transcribe

    with session_scope() as db:
        a = db.get(InterviewAnswer, answer_id)
        s = db.get(InterviewSession, a.session_id)
        m = db.get(MediaFile, a.media_id) if a.media_id else None
        user_id, keep = s.user_id, (s.consent or {}).get("retain_audio")
        path, mime = (str(path_for(m.storage_key)), m.mime) if m and m.retention_status != "deleted" else (None, None)
    if not path:
        with session_scope() as db:
            a = db.get(InterviewAnswer, answer_id)
            a.transcription_status, a.status, a.transcription_error = "unavailable", "review", "Recording not available; type the answer."
        return {"status": "unavailable"}
    try:
        t = await transcribe(user_id, path, mime)
    except AppError as e:
        with session_scope() as db:
            a = db.get(InterviewAnswer, answer_id)
            a.transcription_status, a.status, a.transcription_error = "failed", "review", e.message
        emit("interview_answer", {"answer_id": answer_id, "status": "review", "transcription": "failed", "error": e.message}, user_id)
        return {"status": "failed", "error": e.message}
    with session_scope() as db:
        a = db.get(InterviewAnswer, answer_id)
        a.transcript_raw = t.text
        a.transcript_final = t.text
        a.transcription_provider = t.provider
        a.transcription_status = "done" if t.text.strip() else "empty"
        a.transcription_error = None if t.text.strip() else "No speech detected in the recording."
        a.transcribed_at = utcnow()
        if t.duration and not a.duration_seconds:
            a.duration_seconds = t.duration
        a.status = "review"
        media_id = a.media_id
    if media_id and not keep:
        delete_media(media_id, user_id)
    emit("interview_answer", {"answer_id": answer_id, "status": "review", "transcription": "done"}, user_id)
    return {"status": "done", "chars": len(t.text)}


async def evaluate_answer_job(answer_id: str) -> dict[str, Any]:
    with session_scope() as db:
        a = db.get(InterviewAnswer, answer_id)
        q = db.get(InterviewQuestion, a.question_id)
        s = db.get(InterviewSession, a.session_id)
        user_id = s.user_id
        qd = {"id": q.id, "qtype": q.qtype, "prompt": q.prompt, "rubric": q.rubric, "provenance": q.provenance, "code_context": q.code_context,
              "challenge": q.challenge}
        followups_used = len(db.execute(select(InterviewQuestion.id).where(InterviewQuestion.parent_question_id == (q.parent_question_id or q.id))).all())
        allow_followup = q.followup_depth < q.max_followups and followups_used < q.max_followups and a.answer_source != "none" and s.status == "active"
        transcript, source, duration = a.transcript_final or "", a.answer_source, a.duration_seconds
        skill, kind, q_order, parent = q.target_skill_key, s.kind, q.order_index, q.parent_question_id or q.id
        depth = q.followup_depth
        code_ev = (q.code_context or {}).get("evidence_id") or q.evidence_id
    if source == "none":
        outcome = None
    elif llm_available(user_id):
        try:
            outcome = await llm_evaluate(user_id, qd, transcript, allow_followup)
        except (LLMFailed, NotConfigured) as e:
            outcome = heuristic_evaluate(qd, transcript)
            outcome.scores["method"] += f" (LLM failed: {e.message[:120]})"
    else:
        outcome = heuristic_evaluate(qd, transcript)
    with session_scope() as db:
        a = db.get(InterviewAnswer, answer_id)
        if outcome is None:
            a.status = "evaluated"
            return {"status": "skipped"}
        ev = InterviewEvaluation(answer_id=answer_id, scores=outcome.scores, overall=outcome.overall, strengths=outcome.strengths,
                                 missing_points=outcome.missing_points, correction=outcome.correction, model_answer=outcome.model_answer,
                                 delivery_metrics=delivery_metrics(transcript, duration, source), followup_suggestion=outcome.followup,
                                 provider=outcome.provider, model=outcome.model, evaluator_version=EVALUATOR_VERSION)
        db.add(ev)
        db.flush()
        a.status = "evaluated"
        if outcome.followup and allow_followup:
            nxt = db.execute(select(InterviewQuestion.order_index).where(InterviewQuestion.session_id == a.session_id, InterviewQuestion.order_index > q_order)
                             .order_by(InterviewQuestion.order_index).limit(1)).scalar_one_or_none()
            new_idx = (q_order + nxt) / 2 if nxt is not None else q_order + 1
            fq = InterviewQuestion(session_id=a.session_id, order_index=new_idx, qtype="followup", prompt=outcome.followup, target_skill_key=skill,
                                   evidence_id=qd["code_context"].get("evidence_id") if qd["code_context"] else None, provenance={"kind": "followup", "parent_question_id": qd["id"],
                                                                                                                                   "based_on_answer_id": answer_id},
                                   prep_seconds=20, answer_seconds=120, rubric=qd["rubric"], max_followups=0 if depth + 1 >= 2 else 1,
                                   parent_question_id=parent, followup_depth=depth + 1, code_context=qd["code_context"])
            db.add(fq)
        label_score = outcome.overall
        if label_score >= 70:
            label = "Candidate explained this in this session" if kind == "code_defense" else "Strong answer in this session"
        elif label_score >= 45:
            label = "Partially explained in this session"
        else:
            label = "Not demonstrated during this interview"
        db.add(UnderstandingEvidence(user_id=user_id, session_id=a.session_id, question_id=qd["id"], evaluation_id=ev.id, skill_key=skill,
                                     evidence_type="UNDERSTANDING_EVIDENCE" if kind == "code_defense" else "INTERVIEW_EVIDENCE",
                                     label=label, rubric_score=label_score, code_evidence_id=code_ev if kind == "code_defense" else None))
    emit("interview_answer", {"answer_id": answer_id, "status": "evaluated", "overall": outcome.overall}, user_id)
    return {"status": "evaluated", "overall": outcome.overall}


def advance(user_id: str, session_id: str) -> dict[str, Any]:
    with session_scope() as db:
        s = _session(db, user_id, session_id)
        qs = ordered_questions(db, session_id)
        answered = {qid for (qid,) in db.execute(select(InterviewAnswer.question_id).where(InterviewAnswer.session_id == session_id))}
        nxt = next((i for i, q in enumerate(qs) if q.id not in answered), None)
        s.current_index = nxt if nxt is not None else len(qs)
        return {"current_index": s.current_index, "remaining": sum(1 for q in qs if q.id not in answered)}


def complete(user_id: str, session_id: str) -> dict[str, Any]:
    with session_scope() as db:
        s = _session(db, user_id, session_id)
        s.status = "completed"
        s.ended_at = utcnow()
    rep = build_report(user_id, session_id)
    with session_scope() as db:
        s = db.get(InterviewSession, session_id)
        s.report = rep
    emit("interview_completed", {"session_id": session_id, "overall": rep.get("overall")}, user_id)
    return rep


def build_report(user_id: str, session_id: str) -> dict[str, Any]:
    with session_scope() as db:
        s = _session(db, user_id, session_id)
        qs = ordered_questions(db, session_id)
        items = []
        missing_all: dict[str, int] = defaultdict(int)
        dims: dict[str, list[float]] = defaultdict(list)
        for q in qs:
            a = db.execute(select(InterviewAnswer).where(InterviewAnswer.question_id == q.id)).scalar_one_or_none()
            ev = db.execute(select(InterviewEvaluation).where(InterviewEvaluation.answer_id == a.id)).scalar_one_or_none() if a else None
            if ev:
                for m in ev.missing_points or []:
                    missing_all[m] += 1
                for k, v in (ev.scores or {}).items():
                    if isinstance(v, (int, float)):
                        dims[k].append(float(v))
            items.append({"question_id": q.id, "qtype": q.qtype, "prompt": q.prompt, "skill_key": q.target_skill_key, "status": q.status,
                          "answer_source": a.answer_source if a else None, "overall": ev.overall if ev else None})
        scored = [i["overall"] for i in items if i["overall"] is not None]
        overall = round(sum(scored) / len(scored), 1) if scored else None
        trend_rows = db.execute(select(InterviewSession).where(InterviewSession.user_id == user_id, InterviewSession.status == "completed",
                                                               InterviewSession.kind == s.kind).order_by(InterviewSession.ended_at)).scalars().all()
        trend = [{"session_id": t.id, "ended_at": t.ended_at, "overall": (t.report or {}).get("overall")} for t in trend_rows if t.id != session_id and t.report]
        if overall is not None:
            trend.append({"session_id": session_id, "ended_at": s.ended_at or utcnow(), "overall": overall})
        return {
            "session_id": session_id, "kind": s.kind, "overall": overall,
            "overall_formula": "mean of per-question overall scores; each = mean rubric dimension (1-5) × 20. Coaching heuristic, not a hiring prediction.",
            "answered": sum(1 for i in items if i["answer_source"] not in (None, "none")), "skipped": sum(1 for i in items if i["status"] == "skipped"),
            "dimension_averages": {k: round(sum(v) / len(v), 2) for k, v in dims.items()},
            "recurring_gaps": sorted(({"point": k, "count": v} for k, v in missing_all.items()), key=lambda x: -x["count"])[:8],
            "items": items, "trend": trend, "generated_at": utcnow(),
        }


def delete_session_media(user_id: str, session_id: str) -> int:
    with session_scope() as db:
        _session(db, user_id, session_id)
        ids = [m for (m,) in db.execute(select(InterviewAnswer.media_id).where(InterviewAnswer.session_id == session_id, InterviewAnswer.media_id.is_not(None)))]
    n = sum(1 for m in ids if delete_media(m, user_id))
    audit("media_deleted", {"session_id": session_id, "count": n}, user_id)
    return n


def delete_session(user_id: str, session_id: str) -> None:
    delete_session_media(user_id, session_id)
    with session_scope() as db:
        s = _session(db, user_id, session_id)
        db.delete(s)
        audit("interview_deleted", {"session_id": session_id}, user_id, db=db)
