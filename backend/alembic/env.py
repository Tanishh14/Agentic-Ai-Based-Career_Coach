"""Alembic environment (SQLite, render_as_batch for portable ALTERs)."""
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine

from backend.app import models  # noqa: F401  (register tables)
from backend.app.config import get_settings
from backend.app.database import Base

config = context.config
if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _url() -> str:
    return config.attributes.get("database_url") or get_settings().database_url


def run_migrations_offline() -> None:
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True, render_as_batch=True,
                      dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    # A fresh engine without app-level PRAGMAs: foreign keys stay OFF during migrations so
    # batch-mode table rebuilds work; the application enables them on every connection.
    engine = create_engine(_url())
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, render_as_batch=True, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()
        connection.commit()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
