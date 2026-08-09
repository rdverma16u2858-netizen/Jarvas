"""
Tests for the health endpoints.
═══════════════════════════════════════════════════════════════════════════

WHAT THESE PROVE
    That the whole Phase 0 stack is wired together — settings load, the app
    builds, the lifespan runs, the router mounts at the right prefix, the
    database answers a query, and the cache initialises.

    If every test here passes, the foundation works. If one fails, the message
    says which layer broke.
"""

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


async def test_root_returns_service_info(client: AsyncClient) -> None:
    """GET / should describe the service rather than 404."""
    response = await client.get("/")

    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "MathBot API"
    assert body["health"] == "/api/v1/health"


async def test_liveness_checks_nothing(client: AsyncClient) -> None:
    """The liveness probe must answer without touching any dependency."""
    response = await client.get("/api/v1/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "alive"}


async def test_readiness_passes_when_database_is_up(client: AsyncClient) -> None:
    """Readiness signals through the status code, not the body."""
    response = await client.get("/api/v1/health/ready")

    assert response.status_code == 200
    assert response.json()["status"] == "ready"


async def test_health_reports_every_component(client: AsyncClient) -> None:
    """The full report should list each dependency with a status."""
    response = await client.get("/api/v1/health")

    assert response.status_code == 200
    body = response.json()

    assert set(body["components"]) == {"database", "cache"}
    assert body["environment"] == "local"
    assert body["version"] == "0.1.4"


async def test_database_component_is_up(client: AsyncClient) -> None:
    """The database check must actually execute a query, not assume success."""
    body = (await client.get("/api/v1/health")).json()
    database = body["components"]["database"]

    assert database["status"] == "up"
    assert database["detail"] == "sqlite"
    # A real round trip takes measurable time; None would mean the check was skipped.
    assert database["latency_ms"] is not None


async def test_memory_cache_reports_degraded_not_up(client: AsyncClient) -> None:
    """The in-memory fallback must be visible, not disguised as healthy.

    It works, but it is per-process and empties on restart. Reporting it as
    `up` would hide a real production problem behind a green tick.
    """
    body = (await client.get("/api/v1/health")).json()

    assert body["components"]["cache"]["status"] == "degraded"
    assert "REDIS_URL" in body["components"]["cache"]["detail"]
    # One degraded component drags the overall verdict down, but not to unhealthy.
    assert body["status"] == "degraded"


async def test_docs_are_served_locally(client: AsyncClient) -> None:
    """Interactive docs should be available in local, and are disabled in prod."""
    assert (await client.get("/docs")).status_code == 200
    assert (await client.get("/openapi.json")).status_code == 200


async def test_unknown_route_is_404(client: AsyncClient) -> None:
    """Routes exist only under the API prefix."""
    assert (await client.get("/health")).status_code == 404  # missing /api/v1
    assert (await client.get("/api/v1/nope")).status_code == 404


# ── deployment: the database URL a platform actually hands you ─────────────


async def test_platform_postgres_urls_are_normalised() -> None:
    """Render, Heroku, Railway and Fly all inject the bare scheme.

    Rejecting those means the app cannot read the database its own platform
    just created, and the deploy dies at startup with what looks like a
    configuration mistake rather than a clash of conventions.
    """
    from app.core.config import Settings

    for url in (
        "postgres://u:p@host:5432/db",
        "postgresql://u:p@host:5432/db",
        "postgresql+psycopg2://u:p@host:5432/db",
    ):
        assert Settings(DATABASE_URL=url).DATABASE_URL.startswith("postgresql+asyncpg://")


async def test_an_unusable_driver_is_still_rejected() -> None:
    """Normalising the known-good ones must not turn the check into a no-op."""
    import pytest

    from app.core.config import Settings

    with pytest.raises(Exception, match="async driver"):
        Settings(DATABASE_URL="mysql://u:p@host/db")
