from __future__ import annotations

from fastapi import Request

from ..errors import AppError
from ..services.auth import user_for_token


def bearer_token(request: Request) -> str | None:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    # EventSource and plain links (data export) cannot send headers.
    return request.query_params.get("token")


def user_id(request: Request) -> str:
    """The logged-in student. Every query is filtered by this id."""
    uid = user_for_token(bearer_token(request))
    if uid is None:
        raise AppError("auth_required", "Please log in.", status=401, action="Log in or register with your name and a password.")
    return uid
