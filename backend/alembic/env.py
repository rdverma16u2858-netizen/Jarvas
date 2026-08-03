"""
Alembic environment.
═══════════════════════════════════════════════════════════════════════════

TWO THINGS THIS FILE GETS RIGHT THAT THE DEFAULT TEMPLATE DOES NOT

1. IT READS THE APP'S OWN SETTINGS.
   The generated template puts a database URL in alembic.ini. That URL then
   drifts from the application's, and eventually someone runs a migration
   against the wrong database. Here the URL comes from `app.core.config`, so
   there is exactly one source of truth.

2. IT RUNS ASYNC.
   The app uses an async driver (+aiosqlite / +asyncpg). Alembic's default
   template is synchronous and cannot open those connections at all, so the
   engine is created with `create_async_engine` and the migration body is run
   through `run_sync`.

USE
    alembic revision --autogenerate -m "add conversations"
    alembic upgrade head
    alembic downgrade -1
"""

import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import context
from app.core.config import settings
from app.db.base import Base

# This import registers every table on Base.metadata. Without it, autogenerate
# produces an EMPTY migration and reports no changes — the single most common
# Alembic mistake, and a silent one.
from app.models import *  # noqa: F401,F403  (import for side effects)

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _configure(connection: Connection) -> None:
    """Shared migration context settings."""
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        # Detect column TYPE changes, not just added/dropped columns. Off by
        # default, which means a String(50) -> String(200) change generates an
        # empty migration and fails later at insert time.
        compare_type=True,
        compare_server_default=True,
        # SQLite cannot ALTER most columns. Batch mode rebuilds the table
        # instead, so the same migration script works on SQLite and Postgres —
        # which is the whole point of testing against both in CI.
        render_as_batch=settings.database_kind == "sqlite",
    )


def run_migrations_offline() -> None:
    """Emit SQL to stdout without connecting.

    `alembic upgrade head --sql` — for a DBA who wants to review the
    statements before they touch production.
    """
    context.configure(
        url=settings.DATABASE_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=settings.database_kind == "sqlite",
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """Connect and apply the migrations."""
    engine = create_async_engine(settings.DATABASE_URL, poolclass=pool.NullPool)

    async with engine.connect() as connection:
        await connection.run_sync(lambda sync_conn: _configure(sync_conn))
        await connection.run_sync(lambda _: context.run_migrations())

    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
