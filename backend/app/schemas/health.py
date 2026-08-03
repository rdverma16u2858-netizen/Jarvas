"""
Response shapes for the health endpoints.
═══════════════════════════════════════════════════════════════════════════

WHY PYDANTIC MODELS INSTEAD OF PLAIN DICTS
    Returning `{"status": "ok"}` from a handler works, but a Pydantic model
    buys three things for the same effort:

    1. FastAPI generates accurate OpenAPI docs from it, so /docs shows the
       real response shape instead of "object".
    2. The frontend can generate TypeScript types from that OpenAPI schema —
       one definition, both languages.
    3. Renaming a field breaks loudly here instead of silently returning the
       wrong key to a client.
"""

from typing import Literal

from pydantic import BaseModel, Field


class ComponentHealth(BaseModel):
    """Health of one dependency the API needs."""

    status: Literal["up", "down", "degraded"] = Field(
        description="up = fully working, degraded = working with a fallback, down = failed"
    )
    detail: str = Field(default="", description="Driver, backend, or the error message")
    latency_ms: float | None = Field(
        default=None, description="Round-trip time of the check, when measured"
    )


class HealthResponse(BaseModel):
    """Full health report. Returned by GET /api/v1/health."""

    status: Literal["healthy", "degraded", "unhealthy"] = Field(
        description=(
            "healthy = every component up · "
            "degraded = running on a fallback (e.g. in-memory cache) · "
            "unhealthy = something required is down"
        )
    )
    app: str
    version: str
    environment: str
    components: dict[str, ComponentHealth]

    model_config = {
        "json_schema_extra": {
            "example": {
                "status": "degraded",
                "app": "MathBot API",
                "version": "0.1.0",
                "environment": "local",
                "components": {
                    "database": {
                        "status": "up",
                        "detail": "sqlite",
                        "latency_ms": 1.2,
                    },
                    "cache": {
                        "status": "degraded",
                        "detail": "memory (set REDIS_URL for a shared cache)",
                        "latency_ms": 0.01,
                    },
                },
            }
        }
    }


class LivenessResponse(BaseModel):
    """Returned by GET /api/v1/health/live — process is running, nothing more."""

    status: Literal["alive"] = "alive"
