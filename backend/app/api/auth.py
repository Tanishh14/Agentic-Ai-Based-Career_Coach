"""Register / login / logout (name + password) and the per-job leaderboard."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from ..database import session_scope
from ..models import User
from ..services import auth as auth_svc
from ..services import leaderboard as lb
from ..services.assessments import create_assessment
from ..services.jobs import set_tracked
from .deps import bearer_token, user_id

router = APIRouter(prefix="/api/auth", tags=["auth"])


class Credentials(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    password: str = Field(min_length=1, max_length=200)


@router.post("/register")
def register(body: Credentials) -> dict[str, Any]:
    return auth_svc.register(body.name, body.password)


@router.post("/login")
def login(body: Credentials) -> dict[str, Any]:
    return auth_svc.login(body.name, body.password)


@router.post("/logout")
def logout(request: Request) -> dict[str, Any]:
    tok = bearer_token(request)
    if tok:
        auth_svc.logout(tok)
    return {"ok": True}


@router.get("/me")
def me(uid: str = Depends(user_id)) -> dict[str, Any]:
    with session_scope() as db:
        u = db.get(User, uid)
        return {"id": u.id, "name": u.display_name or u.username, "created_at": u.created_at,
                "show_on_leaderboard": (u.settings or {}).get("show_on_leaderboard", True)}


board = APIRouter(prefix="/api/leaderboard", tags=["leaderboard"])


@board.get("/jobs")
def jobs(uid: str = Depends(user_id)) -> list[dict[str, Any]]:
    return lb.shared_jobs(uid)


@board.get("/job/{job_id}")
def job_board(job_id: str, uid: str = Depends(user_id)) -> dict[str, Any]:
    return lb.leaderboard(job_id, uid)


@board.post("/join/{posting_id}")
def join(posting_id: str, uid: str = Depends(user_id)) -> dict[str, Any]:
    pid = lb.join(posting_id, uid)
    set_tracked(uid, pid)
    create_assessment(pid, "target_selected")
    return {"job_posting_id": pid}
