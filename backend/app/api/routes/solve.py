"""
POST /solve — the product's main endpoint.
═══════════════════════════════════════════════════════════════════════════

Takes a mathematics problem and returns the full ten-part solution together
with an independent verification verdict.

The response carries `verified` at the TOP LEVEL, deliberately. A client
should not have to dig through nested fields to find out whether the answer
was checked — that is the single most important fact about it, and the UI
needs it to decide between showing a green tick and showing a warning.
"""

import json

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.core.logging import get_logger
from app.llm.base import ModelTier
from app.llm.errors import ConfigurationError, RateLimitError
from app.llm.factory import get_provider
from app.math.schema import Solution
from app.math.solver import Solver, SolverError, StreamingSolver
from app.math.verifier import VerdictKind

logger = get_logger(__name__)

router = APIRouter(prefix="/solve", tags=["solve"])


class SolveRequest(BaseModel):
    problem: str = Field(
        min_length=2,
        max_length=6000,
        description="The problem, in plain text or LaTeX",
        examples=["Evaluate the integral of ln(1+x)/(1+x^2) from 0 to 1"],
    )
    tier: ModelTier = Field(
        default=ModelTier.BALANCED,
        description=(
            "fast = routine problems, quickest. "
            "balanced = the default. "
            "deep = proofs and olympiad problems; noticeably slower."
        ),
    )
    use_cache: bool = Field(
        default=True,
        description="False forces a fresh solve, spending quota",
    )


class VerdictResponse(BaseModel):
    """What the computer algebra system concluded."""

    kind: VerdictKind = Field(
        description=(
            "verified = independently recomputed and matched · "
            "refuted = recomputed and did NOT match · "
            "unverifiable = nothing computable to check (a proof) · "
            "error = the check could not run"
        )
    )
    detail: str
    expected: str = Field(default="", description="What SymPy computed, if it differed")
    claimed: str = Field(default="", description="What the model asserted")
    checks: list[str] = Field(default_factory=list)


class AttemptSummary(BaseModel):
    model: str
    verdict: VerdictKind
    latency_ms: float
    cached: bool


class SolveResponse(BaseModel):
    # The headline fact, first.
    verified: bool = Field(description="True ONLY if SymPy independently confirmed the answer")
    solution: Solution
    verdict: VerdictResponse
    attempts: list[AttemptSummary] = Field(
        description="One entry per try. More than one means the first was refuted and retried."
    )
    total_ms: float


@router.post(
    "",
    response_model=SolveResponse,
    summary="Solve a problem and verify the answer",
    responses={
        429: {"description": "Provider rate limit"},
        502: {"description": "The model could not be reached or gave unusable output"},
        503: {"description": "No LLM provider configured"},
    },
)
async def solve(request: SolveRequest) -> SolveResponse:
    """Solve, verify, and retry once if the verification fails."""
    try:
        provider = get_provider()
    except ConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc

    try:
        result = await Solver(provider).solve(
            request.problem, tier=request.tier, use_cache=request.use_cache
        )
    except RateLimitError as exc:
        headers = {"Retry-After": str(int(exc.retry_after))} if exc.retry_after else None
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                "Daily quota exhausted — resets tomorrow."
                if exc.daily
                else f"Rate limited. {exc.message}"
            ),
            headers=headers,
        ) from exc
    except SolverError as exc:
        logger.exception("solve failed")
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    return SolveResponse(
        verified=result.verified,
        solution=result.solution,
        verdict=VerdictResponse(**result.verdict.__dict__),
        attempts=[
            AttemptSummary(
                model=a.model,
                verdict=a.verdict.kind,
                latency_ms=a.latency_ms,
                cached=a.cached,
            )
            for a in result.attempts
        ],
        total_ms=result.total_ms,
    )


@router.post(
    "/stream",
    summary="Solve with live progress (Server-Sent Events)",
    response_class=StreamingResponse,
)
async def solve_stream(request: SolveRequest) -> StreamingResponse:
    """Stream the solve as it happens, as Server-Sent Events.

    WHY SSE RATHER THAN A WEBSOCKET
        The data flows one way — server to browser — and SSE is plain HTTP:
        it reconnects on its own, needs no protocol upgrade, and passes
        through proxies that block WebSocket upgrades. A WebSocket would add a
        second transport to maintain for no benefit.

    EVENT TYPES
        stage   {"type":"stage","stage":"solving"|"verifying","message":...}
        delta   {"type":"delta","text":"..."}          raw generated characters
        result  {"type":"result", ...the full verified solution}
        error   {"type":"error","message":...}

    Errors are sent as an EVENT, not an HTTP status. Once the response has
    begun streaming the status code is already on the wire and cannot be
    changed — a client that only checks `response.ok` would see 200 and then
    silence. Delivering the failure in-band is the only way it can be shown.
    """
    try:
        provider = get_provider()
    except ConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc

    def sse(body: dict) -> str:
        """Format one Server-Sent Event.

        The blank line after the data line is what terminates an event in the
        SSE wire format. Omit it and the browser buffers everything, waiting
        for a boundary that never arrives — the stream appears to hang.
        """
        return f"data: {json.dumps(body)}\n\n"

    async def events():
        solver = StreamingSolver(provider)
        try:
            async for event in solver.solve_stream(request.problem, tier=request.tier):
                if event.type == "result":
                    yield sse({"type": "result", **(event.payload or {})})
                elif event.type == "delta":
                    yield sse({"type": "delta", "text": event.text})
                elif event.type == "stage":
                    yield sse(
                        {
                            "type": "stage",
                            "stage": event.stage,
                            "message": event.message,
                        }
                    )
                else:
                    yield sse({"type": "error", "message": event.message})
        except RateLimitError as exc:
            yield sse(
                {
                    "type": "error",
                    "message": (
                        "Daily quota exhausted - resets tomorrow."
                        if exc.daily
                        else f"Rate limited. {exc.message}"
                    ),
                }
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("streaming solve failed")
            yield sse({"type": "error", "message": str(exc)})
        finally:
            # Tells the client the stream ended on purpose, so it can close
            # cleanly instead of waiting for a timeout.
            yield sse({"type": "done"})

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            # Without this, nginx buffers the whole response and the "stream"
            # arrives in one lump at the end - the classic SSE-behind-a-proxy
            # failure, which looks exactly like the feature not working.
            "X-Accel-Buffering": "no",
        },
    )
