"""Agent runs/actions, background jobs, tool registry and the SSE event stream."""
from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import func, or_, select

from ..agents.orchestrator import STANDING_GOAL, TOOL_REGISTRY
from ..database import session_scope
from ..errors import AppError
from ..models import AgentAction, AgentRun, EventLog
from ..services import job_queue
from .deps import user_id

router = APIRouter(prefix="/api/agent", tags=["agent"])


@router.get("/tools")
def tools() -> dict[str, Any]:
    return {"standing_goal": STANDING_GOAL, "tools": TOOL_REGISTRY}


@router.get("/runs")
def runs(limit: int = 50, uid: str = Depends(user_id)) -> list[dict[str, Any]]:
    with session_scope() as db:
        rows = db.execute(select(AgentRun).where(or_(AgentRun.user_id == uid, AgentRun.user_id.is_(None)))
                          .order_by(AgentRun.started_at.desc()).limit(min(limit, 200))).scalars()
        return [{"id": r.id, "trigger": r.trigger, "goal": r.goal, "status": r.status, "started_at": r.started_at, "ended_at": r.ended_at,
                 "step_count": r.step_count, "llm_calls": r.llm_calls, "model_provider": r.model_provider, "model_id": r.model_id,
                 "summary": r.summary, "error_summary": r.error_summary} for r in rows]


@router.get("/runs/{run_id}")
def run_detail(run_id: str, uid: str = Depends(user_id)) -> dict[str, Any]:
    with session_scope() as db:
        r = db.get(AgentRun, run_id)
        if r is None or (r.user_id and r.user_id != uid):
            raise AppError("not_found", "Run not found.", status=404)
        acts = db.execute(select(AgentAction).where(AgentAction.run_id == run_id).order_by(AgentAction.seq)).scalars()
        return {"id": r.id, "trigger": r.trigger, "goal": r.goal, "status": r.status, "started_at": r.started_at, "ended_at": r.ended_at,
                "summary": r.summary, "error_summary": r.error_summary, "llm_calls": r.llm_calls,
                "actions": [{"seq": a.seq, "action": a.action, "tool": a.tool, "input_summary": a.input_summary, "result_summary": a.result_summary,
                             "entity_ids": a.entity_ids, "status": a.status, "duration_ms": a.duration_ms, "created_at": a.created_at} for a in acts]}


@router.get("/jobs")
def jobs(uid: str = Depends(user_id)) -> dict[str, Any]:
    return {"stats": job_queue.queue_stats(), "recent": job_queue.recent_jobs(40, uid)}


@router.post("/jobs/{job_id}/retry")
def retry(job_id: str, uid: str = Depends(user_id)) -> dict[str, Any]:
    return {"retried": job_queue.retry_dead(job_id)}


@router.get("/events/recent")
def recent_events(limit: int = 100, uid: str = Depends(user_id)) -> list[dict[str, Any]]:
    with session_scope() as db:
        rows = db.execute(select(EventLog).where(or_(EventLog.user_id == uid, EventLog.user_id.is_(None))).order_by(EventLog.seq.desc())
                          .limit(min(limit, 500))).scalars()
        return [{"seq": e.seq, "type": e.type, "payload": e.payload, "created_at": e.created_at} for e in rows]


events_router = APIRouter(tags=["events"])


@events_router.get("/api/events")
async def events(request: Request, after: int | None = None, uid: str = Depends(user_id)) -> StreamingResponse:
    """Server-Sent Events tail of the event_log table (works across API and worker processes)."""
    last = after
    if last is None:
        hdr = request.headers.get("last-event-id")
        if hdr and hdr.isdigit():
            last = int(hdr)
    if last is None:
        with session_scope() as db:
            last = db.execute(select(func.max(EventLog.seq))).scalar() or 0

    async def gen():
        nonlocal last
        yield "retry: 3000\n\n"
        idle = 0
        while True:
            if await request.is_disconnected():
                break
            with session_scope() as db:
                rows = db.execute(select(EventLog).where(EventLog.seq > last, or_(EventLog.user_id == uid, EventLog.user_id.is_(None)))
                                  .order_by(EventLog.seq).limit(200)).scalars().all()
                batch = [(e.seq, e.type, e.payload, e.created_at) for e in rows]
            for seq, typ, payload, at in batch:
                last = seq
                yield f"id: {seq}\nevent: {typ}\ndata: {json.dumps({'type': typ, 'payload': payload, 'at': at})}\n\n"
            if batch:
                idle = 0
            else:
                idle += 1
                if idle % 15 == 0:
                    yield ": keep-alive\n\n"
            await asyncio.sleep(1.0)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
