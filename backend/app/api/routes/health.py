"""
Health endpoints.
═══════════════════════════════════════════════════════════════════════════

WHY THREE ENDPOINTS AND NOT ONE
    They answer different questions and are consumed by different things:

    GET /health        Full report — which dependencies are up, how fast they
                       answered. For humans debugging, and for a dashboard.

    GET /health/live   "Is the process running?" Nothing else. A container
                       orchestrator restarts the container when this fails, so
                       it must NOT check the database — a database blip would
                       otherwise trigger a restart loop that fixes nothing.

    GET /health/ready  "Should traffic be routed here?" This DOES check
                       dependencies. A load balancer pulls the instance out of
                       rotation when it fails, then puts it back when the
                       database recovers. No restart, no data loss.

    Conflating liveness and readiness is the classic Kubernetes mistake, which
    is why they are separate from day one.
"""

import time

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache.client import cache
from app.core.config import settings
from app.core.logging import get_logger
from app.db.session import get_db
from app.schemas.health import ComponentHealth, HealthResponse, LivenessResponse

logger = get_logger(__name__)

router = APIRouter(prefix="/health", tags=["health"])


async def _check_database(db: AsyncSession) -> ComponentHealth:
    """Run the cheapest possible query to prove the connection works.

    `SELECT 1` touches no tables, so it stays valid before any migration has
    run — which matters, because this endpoint must work on a fresh database.
    """
    started = time.perf_counter()
    try:
        await db.execute(text("SELECT 1"))
        return ComponentHealth(
            status="up",
            detail=settings.database_kind,
            latency_ms=round((time.perf_counter() - started) * 1000, 2),
        )
    except Exception as exc:  # noqa: BLE001 — report any failure, never raise
        logger.exception("database health check failed")
        return ComponentHealth(status="down", detail=str(exc)[:200])


async def _check_cache() -> ComponentHealth:
    """Ping the cache and report which backend is actually serving.

    The in-memory backend reports `degraded`, not `up`. It works, but it is
    per-process and empties on restart — a real limitation that should be
    visible rather than hidden behind a green tick.
    """
    started = time.perf_counter()
    try:
        alive = await cache.ping()
        latency = round((time.perf_counter() - started) * 1000, 3)

        if not alive:
            return ComponentHealth(
                status="down", detail=f"{cache.backend} not responding", latency_ms=latency
            )
        if cache.backend == "memory":
            return ComponentHealth(
                status="degraded",
                detail="memory (set REDIS_URL for a shared cache)",
                latency_ms=latency,
            )
        return ComponentHealth(status="up", detail=cache.backend, latency_ms=latency)
    except Exception as exc:  # noqa: BLE001
        logger.exception("cache health check failed")
        return ComponentHealth(status="down", detail=str(exc)[:200])


@router.get(
    "",
    response_model=HealthResponse,
    summary="Full health report",
    description="Checks every dependency and reports status plus latency for each.",
)
async def health(db: AsyncSession = Depends(get_db)) -> HealthResponse:
    """Aggregate the component checks into one overall verdict.

    Rules: any component down -> unhealthy. Any degraded -> degraded.
    Otherwise healthy. Always returns HTTP 200 — the JSON body carries the
    verdict, so a monitoring tool can distinguish "the API answered and said
    the database is down" from "the API did not answer at all".
    """
    components = {
        "database": await _check_database(db),
        "cache": await _check_cache(),
    }

    statuses = {c.status for c in components.values()}
    if "down" in statuses:
        overall = "unhealthy"
    elif "degraded" in statuses:
        overall = "degraded"
    else:
        overall = "healthy"

    return HealthResponse(
        status=overall,
        app=settings.APP_NAME,
        version=settings.APP_VERSION,
        environment=settings.ENV,
        components=components,
    )


@router.get(
    "/live",
    response_model=LivenessResponse,
    summary="Liveness probe",
    description="Returns 200 if the process is running. Checks no dependencies.",
)
async def live() -> LivenessResponse:
    """Deliberately trivial — see the module docstring for why."""
    return LivenessResponse()


@router.get(
    "/ready",
    summary="Readiness probe",
    description="Returns 200 when able to serve traffic, 503 when not.",
    responses={503: {"description": "A required dependency is unavailable"}},
)
async def ready(response: Response, db: AsyncSession = Depends(get_db)) -> dict[str, str]:
    """Report whether this instance should receive traffic.

    Unlike /health, this signals through the STATUS CODE, because load
    balancers route on the code and do not parse the body.

    The cache is intentionally not a gate: the in-memory fallback means a
    Redis outage degrades performance but does not stop the app serving
    correct answers. Losing the database does.
    """
    database = await _check_database(db)
    if database.status == "down":
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "not_ready", "reason": "database unavailable"}
    return {"status": "ready"}
