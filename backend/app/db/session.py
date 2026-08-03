"""
Database engine and session management.
═══════════════════════════════════════════════════════════════════════════

WHY THIS FILE EXISTS
    Two things every other module needs and neither should build itself:

    1. `engine`  — the connection pool. Created once for the whole process.
       Creating one per request would open a new TCP connection every time and
       exhaust the database's connection limit under load.

    2. `get_db`  — a FastAPI dependency that hands each request its own
       session and guarantees it is closed afterwards, even if the handler
       raises.

WHY ASYNC
    A math request spends most of its wall-clock time waiting — on the LLM, on
    the database, on SymPy. Async lets one worker serve other requests during
    that wait instead of blocking. That is the entire reason FastAPI was
    chosen over Flask.
"""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings


def _build_engine() -> AsyncEngine:
    """Create the connection pool, tuned for whichever database is configured.

    SQLite and Postgres need genuinely different settings, so this branches
    rather than pretending one size fits both:

    · SQLite is a file, not a server. It has no connection limit to manage and
      rejects the pool-size arguments entirely, so they are omitted.

    · Postgres is a server with a hard `max_connections`. `pool_size` is the
      steady-state number of connections held open; `max_overflow` is how many
      extra may be opened during a burst. `pool_pre_ping` sends a cheap
      liveness check before handing over a connection, which prevents the
      classic "server closed the connection unexpectedly" error after a
      database restart or an idle-timeout kill by a proxy.
    """
    common = {"echo": settings.DATABASE_ECHO, "future": True}

    if settings.database_kind == "sqlite":
        return create_async_engine(settings.DATABASE_URL, **common)

    return create_async_engine(
        settings.DATABASE_URL,
        pool_size=10,
        max_overflow=20,
        pool_pre_ping=True,
        # Recycle connections older than 30 min — many managed Postgres hosts
        # silently drop long-idle connections.
        pool_recycle=1800,
        **common,
    )


# One engine per process, created at import. Holds no connections until used.
engine: AsyncEngine = _build_engine()

# Factory that stamps out sessions bound to the engine above.
#
# expire_on_commit=False is deliberate: with the default (True), SQLAlchemy
# invalidates every loaded object after commit, so reading `user.name` from an
# object you just saved fires a fresh SELECT — and in async code that raises
# MissingGreenlet if it happens after the session closed. Turning it off lets
# handlers return objects they just committed.
SessionFactory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency yielding one database session per request.

    Used as:

        @router.get("/items")
        async def list_items(db: AsyncSession = Depends(get_db)):
            ...

    The `async with` block closes the session when the request finishes —
    including when the handler raises, which is what stops a failed request
    from leaking a connection out of the pool.

    Transactions are NOT committed automatically. A handler that writes calls
    `await db.commit()` itself, so a request that fails halfway leaves no
    half-written rows behind.
    """
    async with SessionFactory() as session:
        yield session


async def dispose_engine() -> None:
    """Close every pooled connection. Called from the app's shutdown hook.

    Without this, shutting down leaves connections open on the database side
    until it times them out — which, on a small Postgres instance restarted
    often during development, is enough to exhaust `max_connections`.
    """
    await engine.dispose()
