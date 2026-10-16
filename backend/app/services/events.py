"""Event log (SSE source) and audit trail helpers."""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from ..database import session_scope
from ..models import AuditEvent, EventLog


def emit(event_type: str, payload: dict[str, Any], user_id: str | None = None, db: Session | None = None) -> None:
    """Append an event; the SSE endpoint tails this table, so it works across processes."""
    if db is not None:
        db.add(EventLog(type=event_type, payload=payload, user_id=user_id))
        return
    with session_scope() as s:
        s.add(EventLog(type=event_type, payload=payload, user_id=user_id))


def audit(kind: str, detail: dict[str, Any], user_id: str | None = None, db: Session | None = None) -> None:
    if db is not None:
        db.add(AuditEvent(kind=kind, detail=detail, user_id=user_id))
        return
    with session_scope() as s:
        s.add(AuditEvent(kind=kind, detail=detail, user_id=user_id))
