"""SQLite engine/session setup.

Conventions (documented in README):
- Timestamps are ISO-8601 UTC text, e.g. 2026-10-16T12:00:00.123456+00:00.
- IDs are UUID4 hex text.
- JSON is stored as validated JSON text (JSONText type), never JSONB.
- Every connection runs PRAGMA foreign_keys=ON, journal_mode=WAL and busy_timeout.
"""
from __future__ import annotations

import json
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.types import Text, TypeDecorator

from .config import get_settings


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id() -> str:
    return uuid.uuid4().hex


class JSONText(TypeDecorator):
    """JSON stored as TEXT. Values must be JSON-serialisable."""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Any) -> str | None:
        if value is None:
            return None
        return json.dumps(value, sort_keys=True, ensure_ascii=False)

    def process_result_value(self, value: str | None, dialect: Any) -> Any:
        if value is None or value == "":
            return None
        return json.loads(value)


class Base(DeclarativeBase):
    pass


WAL_ENABLED: dict[str, bool] = {}


def _make_engine(url: str) -> Engine:
    if url.startswith("sqlite:///"):
        db_path = Path(url.replace("sqlite:///", "", 1))
        if str(db_path) != ":memory:":
            db_path.parent.mkdir(parents=True, exist_ok=True)
    eng = create_engine(url, connect_args={"check_same_thread": False, "timeout": 30}, future=True)

    @event.listens_for(eng, "connect")
    def _on_connect(dbapi_conn, _record):  # pragma: no cover - exercised implicitly
        # Disable pysqlite's implicit BEGIN so we can issue BEGIN IMMEDIATE ourselves.
        dbapi_conn.isolation_level = None
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA busy_timeout=10000")
        mode = cur.execute("PRAGMA journal_mode=WAL").fetchone()
        WAL_ENABLED[url] = bool(mode and str(mode[0]).lower() == "wal")
        if not WAL_ENABLED[url]:
            # Filesystem without WAL support: fall back to DELETE journal (documented).
            cur.execute("PRAGMA journal_mode=DELETE")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.close()

    @event.listens_for(eng, "begin")
    def _on_begin(conn):  # pragma: no cover
        conn.exec_driver_sql("BEGIN IMMEDIATE" if conn.info.get("begin_immediate") else "BEGIN")

    return eng


_engine: Engine | None = None
_SessionLocal: sessionmaker | None = None


def get_engine() -> Engine:
    global _engine, _SessionLocal
    if _engine is None:
        _engine = _make_engine(get_settings().database_url)
        _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    return _engine


def configure_engine(url: str) -> Engine:
    """Used by tests to point at a temporary on-disk database."""
    global _engine, _SessionLocal
    if _engine is not None:
        _engine.dispose()
    _engine = _make_engine(url)
    _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    return _engine


def SessionLocal() -> Session:
    get_engine()
    assert _SessionLocal is not None
    return _SessionLocal()


@contextmanager
def session_scope() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@contextmanager
def immediate_transaction() -> Iterator[Session]:
    """Short write transaction that takes the SQLite RESERVED lock up front.

    Used for queue claims and idempotent inserts so two workers cannot claim the
    same job. Never make network calls inside this block.
    """
    eng = get_engine()
    conn = eng.connect()
    conn.info["begin_immediate"] = True
    try:
        trans = conn.begin()
        db = Session(bind=conn, expire_on_commit=False)
        try:
            yield db
            db.flush()
            db.close()
            trans.commit()
        except Exception:
            db.close()
            trans.rollback()
            raise
    finally:
        conn.info.pop("begin_immediate", None)
        conn.close()


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def db_health() -> dict[str, Any]:
    eng = get_engine()
    with eng.connect() as conn:
        fk = conn.execute(text("PRAGMA foreign_keys")).scalar()
        jm = conn.execute(text("PRAGMA journal_mode")).scalar()
        try:
            ver = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
        except Exception:
            ver = None
    return {"url": str(eng.url), "foreign_keys": bool(fk), "journal_mode": jm, "migration_version": ver}
