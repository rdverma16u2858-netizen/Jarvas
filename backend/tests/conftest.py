"""
Shared pytest fixtures.
═══════════════════════════════════════════════════════════════════════════

WHY THIS FILE EXISTS
    pytest loads `conftest.py` automatically, so anything defined here is
    available to every test without an import. It is where the test database
    and the HTTP client get built.

THE TWO RULES THESE FIXTURES ENFORCE
    1. Tests never touch the development database. They get their own SQLite
       file, created before the test and deleted after.
    2. Tests never make real network calls. Requests go through ASGITransport,
       which calls the app in-process — no port, no server, milliseconds
       instead of seconds.
"""

import os
from collections.abc import AsyncGenerator

import pytest_asyncio

# Set BEFORE importing the app: Settings reads the environment at import time,
# so assigning these afterwards would have no effect.
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./test_mathbot.db"
os.environ["REDIS_URL"] = ""  # force the in-memory cache — tests must not need Redis
os.environ["ENV"] = "local"

from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from app.cache.client import cache  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session import SessionFactory, engine  # noqa: E402
from app.main import app  # noqa: E402


@pytest_asyncio.fixture(scope="function")
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """A clean database for one test.

    Tables are created before and dropped after, so tests cannot leak state
    into each other — the failure mode where a suite passes in order and fails
    when run individually.

    `create_all` is used rather than running migrations because it is far
    faster and this asserts the models are correct. Migrations get their own
    test once they exist.
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with SessionFactory() as session:
        yield session

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture(scope="function")
async def client() -> AsyncGenerator[AsyncClient, None]:
    """An HTTP client wired directly to the app, with the lifespan run.

    ASGITransport calls the app in-process instead of over a socket, so no
    server needs to start and no port can conflict in CI.

    The manual lifespan calls matter: `cache.init()` runs in the lifespan hook,
    and without it every cache call raises "used before init()". Entering the
    context manager is what makes the tests exercise the same startup path as
    production.
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture(autouse=True)
async def _clear_cache() -> AsyncGenerator[None, None]:
    """Empty the cache between tests.

    `autouse=True` applies this to every test without it being requested. A
    cached value surviving into the next test is the kind of bug that only
    appears when the suite runs in a particular order.
    """
    yield
    if cache.backend != "uninitialised":
        await cache.close()
