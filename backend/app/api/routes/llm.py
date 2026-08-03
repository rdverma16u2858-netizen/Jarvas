"""
LLM status and a smoke-test endpoint.
═══════════════════════════════════════════════════════════════════════════

    GET  /llm/status   Which provider and models are configured, and whether
                       the key actually works. Costs no generation quota.

    POST /llm/ask      Send a prompt straight through to the model. A
                       development tool for confirming the pipeline works
                       before the real solver exists — NOT the solve endpoint.
                       That arrives in Phase 2 with SymPy verification, and
                       this one deliberately has none.

WHY /ask IS RESTRICTED TO LOCAL
    It is an unauthenticated passthrough to a paid-or-rate-limited model. On a
    public deployment that is an open invitation to burn the quota, so it 404s
    outside local. Phase 11 adds real auth and rate limiting.
"""

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.logging import get_logger
from app.llm.base import ModelTier
from app.llm.errors import ConfigurationError, LLMError, RateLimitError
from app.llm.factory import get_provider

logger = get_logger(__name__)

router = APIRouter(prefix="/llm", tags=["llm"])


class ProviderStatusResponse(BaseModel):
    provider: str
    reachable: bool
    models: dict[str, str] = Field(description="tier -> model id")
    detail: str
    key_fingerprint: str = Field(
        description="First and last 4 characters only — never the full key"
    )


class AskRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=8000)
    tier: ModelTier = Field(
        default=ModelTier.BALANCED,
        description="fast = quick and cheap, balanced = default, deep = hard problems",
    )
    system: str | None = Field(default=None, max_length=8000)
    use_cache: bool = Field(
        default=True, description="False forces a fresh call and spends quota"
    )


class AskResponse(BaseModel):
    text: str
    model: str
    provider: str
    latency_ms: float
    cached: bool
    input_tokens: int | None = None
    output_tokens: int | None = None
    thinking_tokens: int | None = None


@router.get(
    "/status",
    response_model=ProviderStatusResponse,
    summary="Provider and model configuration",
)
async def llm_status() -> ProviderStatusResponse:
    """Verify the provider is configured and reachable.

    Lists models rather than generating text, so calling it repeatedly costs
    nothing. `reachable` is False if a configured model is missing from the
    account — the exact failure that would otherwise surface as a confusing
    404 on the first real question.

    A misconfiguration is REPORTED here, not raised. Returning 500 with a
    stack trace would be self-defeating: the one endpoint whose job is to
    explain what is broken must keep working when things are broken.
    """
    try:
        provider = get_provider()
    except ConfigurationError as exc:
        return ProviderStatusResponse(
            provider=settings.LLM_PROVIDER,
            reachable=False,
            models={},
            detail=str(exc),
            key_fingerprint="not set",
        )

    result = await provider.check()
    return ProviderStatusResponse(**result.__dict__)


@router.post(
    "/ask",
    response_model=AskResponse,
    summary="Send a raw prompt to the model (local only)",
    responses={
        404: {"description": "Not available outside local"},
        429: {"description": "Rate limited by the provider"},
        502: {"description": "The provider failed"},
    },
)
async def ask(request: AskRequest) -> AskResponse:
    """Raw passthrough for manual testing. No verification, no maths logic."""
    if not settings.is_local:
        raise HTTPException(status_code=404)

    provider = get_provider()
    try:
        result = await provider.complete(
            request.prompt,
            tier=request.tier,
            system=request.system,
            use_cache=request.use_cache,
            cache_ttl=settings.LLM_CACHE_TTL,
        )
    except RateLimitError as exc:
        # 429 with Retry-After so a client can back off properly rather than
        # hammering a limit that is already exhausted.
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
    except LLMError as exc:
        logger.exception("llm request failed")
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    return AskResponse(
        text=result.text,
        model=result.model,
        provider=result.provider,
        latency_ms=result.latency_ms,
        cached=result.cached,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        thinking_tokens=result.thinking_tokens,
    )
