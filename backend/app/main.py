"""FastAPI application entrypoint.

Run:  python -m uvicorn backend.app.main:app --port 8000
"""
from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .api import agent, assessments, auth, github, interviews, jobs, profile, proof, system
from .config import get_settings
from .errors import AppError
from .security import redact
from .services.migrations import ensure_migrated

log = logging.getLogger("api")

app = FastAPI(
    title="AI Career Coach — Evidence-Driven (W-PS-2)",
    description="Real job requirements → commit-pinned GitHub evidence → gap matrix → automatic reassessment on push; "
                "LeetCode/profile signals, JD-specific mock interviews, code-defense, gap-to-proof issues, signed skill passports.",
    version="3.0.0",
)

s = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=[s.frontend_base_url, "http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _startup() -> None:
    ensure_migrated()


@app.exception_handler(AppError)
async def app_error(_: Request, exc: AppError) -> JSONResponse:
    headers = {"Retry-After": str(exc.retry_after)} if exc.retry_after else None
    return JSONResponse(exc.to_dict(), status_code=exc.status, headers=headers)


@app.exception_handler(RequestValidationError)
async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    errs = [{"loc": list(e.get("loc", [])), "msg": e.get("msg")} for e in exc.errors()][:10]
    return JSONResponse({"error": {"code": "validation_error", "message": "; ".join(f"{'.'.join(map(str, e['loc'][1:]))}: {e['msg']}" for e in errs),
                                   "detail": errs}}, status_code=422)


@app.exception_handler(Exception)
async def unhandled(_: Request, exc: Exception) -> JSONResponse:
    log.exception("unhandled error")
    return JSONResponse({"error": {"code": "internal_error", "message": redact(f"{type(exc).__name__}: {exc}")[:400],
                                   "action": "Retry; if it persists check the backend log."}}, status_code=500)


for r in (auth.router, auth.board, system.router, jobs.router, jobs.disc, github.router, assessments.router, profile.router, profile.lc, interviews.router,
          proof.router, proof.public, agent.router, agent.events_router):
    app.include_router(r)
