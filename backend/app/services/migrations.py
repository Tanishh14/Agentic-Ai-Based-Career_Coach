"""Run Alembic migrations programmatically (schema is never created with create_all)."""
from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config

from ..config import ROOT_DIR, get_settings


def alembic_config(database_url: str | None = None) -> Config:
    cfg = Config(str(ROOT_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(Path(ROOT_DIR) / "backend" / "alembic"))
    cfg.attributes["configure_logger"] = False
    cfg.attributes["database_url"] = database_url or get_settings().database_url
    return cfg


def ensure_migrated(database_url: str | None = None) -> None:
    """Upgrade to head under an inter-process file lock (API and worker may start together)."""
    import os
    import time

    lock = Path(get_settings().data_dir) / ".migrate.lock"
    deadline = time.time() + 60
    fd = None
    while fd is None:
        try:
            fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            if time.time() - lock.stat().st_mtime > 120:  # stale lock from a crashed process
                lock.unlink(missing_ok=True)
            elif time.time() > deadline:
                raise RuntimeError("Timed out waiting for another process to finish migrations")
            time.sleep(0.3)
    try:
        command.upgrade(alembic_config(database_url), "head")
    finally:
        os.close(fd)
        lock.unlink(missing_ok=True)
