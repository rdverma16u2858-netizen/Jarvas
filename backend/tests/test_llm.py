"""
Tests for the LLM provider layer.
═══════════════════════════════════════════════════════════════════════════

WHAT THESE PROVE
    · The abstraction holds — MockProvider satisfies the same interface the
      solver will use, so Phase 2 can be built and tested with no API key.
    · Caching works, and its key is sensitive to the things that change an
      answer (model, prompt, system) — a cache that returns a flash-lite reply
      to a deep-tier request would be a silent correctness bug.
    · Gemini's error translation is right, especially the 429 that means
      "no free quota" versus the 429 that means "slow down". Getting those
      backwards causes a retry loop that burns the daily allowance.

NO NETWORK
    Every test here runs against MockProvider or a stubbed HTTP response.
    The suite must stay fast, deterministic, and free.
"""

import httpx
import pytest

from app.cache.client import cache
from app.llm.base import LLMResponse, Message, ModelTier
from app.llm.errors import ConfigurationError, ModelUnavailableError, RateLimitError
from app.llm.factory import get_provider, reset_provider
from app.llm.mock import MockProvider

pytestmark = pytest.mark.asyncio


# ── the abstraction ───────────────────────────────────────────────────────


async def test_mock_answers_a_known_problem() -> None:
    cache.init()
    provider = MockProvider()

    result = await provider.complete("What is the derivative of x^2?")

    assert "2x" in result.text
    assert result.provider == "mock"
    assert isinstance(result, LLMResponse)


async def test_string_prompt_is_accepted_like_a_message_list() -> None:
    """complete("...") should behave identically to complete([Message(...)])."""
    cache.init()
    provider = MockProvider()

    from_string = await provider.complete("solve x^2 = 4")
    from_list = await provider.complete([Message(role="user", content="solve x^2 = 4")])

    assert from_string.text == from_list.text


async def test_tiers_resolve_to_different_models() -> None:
    provider = MockProvider()

    assert provider.model_for(ModelTier.FAST) != provider.model_for(ModelTier.DEEP)


async def test_streaming_yields_chunks() -> None:
    provider = MockProvider()

    chunks = [c async for c in provider.stream("What is the derivative of x^2?")]

    assert len(chunks) > 1, "streaming should produce more than one chunk"
    assert "2x" in "".join(chunks)


async def test_json_schema_request_returns_a_schema_valid_solution() -> None:
    """The mock must satisfy the REAL schema, not a convenient stub.

    A mock that returns `{"answer": "mock"}` passes its own test and then
    every caller built against it breaks on the real provider. Validating
    against the actual Solution model is what makes the mock trustworthy
    enough to develop the whole frontend against.
    """
    from app.math.schema import SOLUTION_SCHEMA, Solution

    cache.init()
    provider = MockProvider()

    result = await provider.complete("solve the integral", json_schema=SOLUTION_SCHEMA)

    solution = Solution.model_validate_json(result.text)  # raises if invalid
    assert solution.steps, "the mock should provide real steps to render"
    assert solution.verification.kind.value == "definite_integral"


async def test_mock_can_produce_a_deliberately_wrong_answer() -> None:
    """The refuted UI state needs a reproducible way to trigger it.

    Waiting for a real model to make a real mistake is not a development
    workflow, so 'wrong' in the prompt makes the mock emit a claim SymPy
    will reject.
    """
    from app.math.schema import SOLUTION_SCHEMA, Solution
    from app.math.verifier import VerdictKind, verify_sync

    cache.init()
    provider = MockProvider()

    result = await provider.complete(
        "give me a wrong answer", json_schema=SOLUTION_SCHEMA, use_cache=False
    )
    solution = Solution.model_validate_json(result.text)

    assert verify_sync(solution.verification).kind is VerdictKind.REFUTED


# ── caching ───────────────────────────────────────────────────────────────


async def test_second_identical_call_is_served_from_cache() -> None:
    cache.init()
    provider = MockProvider()

    first = await provider.complete("solve x^2 = 4")
    second = await provider.complete("solve x^2 = 4")

    assert first.cached is False
    assert second.cached is True
    assert first.text == second.text


async def test_use_cache_false_bypasses_the_cache() -> None:
    """Question generation needs fresh output, or every quiz is identical."""
    cache.init()
    provider = MockProvider()

    await provider.complete("solve x^2 = 4")
    fresh = await provider.complete("solve x^2 = 4", use_cache=False)

    assert fresh.cached is False


async def test_cache_key_separates_tiers() -> None:
    """A cheap model's answer must never be served to a deep-tier request."""
    cache.init()
    provider = MockProvider()

    await provider.complete("solve x^2 = 4", tier=ModelTier.FAST)
    deep = await provider.complete("solve x^2 = 4", tier=ModelTier.DEEP)

    assert deep.cached is False
    assert deep.model == provider.model_for(ModelTier.DEEP)


async def test_cache_key_separates_system_prompts() -> None:
    """Changing the system instruction changes the answer, so it must miss."""
    cache.init()
    provider = MockProvider()

    await provider.complete("solve x^2 = 4", system="Be brief.")
    other = await provider.complete("solve x^2 = 4", system="Be thorough.")

    assert other.cached is False


# ── factory ───────────────────────────────────────────────────────────────


async def test_factory_returns_the_configured_provider(monkeypatch) -> None:
    from app.core.config import settings

    reset_provider()
    monkeypatch.setattr(settings, "LLM_PROVIDER", "mock")

    assert get_provider().name == "mock"
    reset_provider()


async def test_unknown_provider_name_is_rejected(monkeypatch) -> None:
    from app.core.config import settings

    reset_provider()
    monkeypatch.setattr(settings, "LLM_PROVIDER", "not-a-provider")

    with pytest.raises(ConfigurationError, match="not implemented"):
        get_provider()
    reset_provider()


async def test_factory_caches_the_instance(monkeypatch) -> None:
    from app.core.config import settings

    reset_provider()
    monkeypatch.setattr(settings, "LLM_PROVIDER", "mock")

    assert get_provider() is get_provider()
    reset_provider()


# ── Gemini error translation (no network) ─────────────────────────────────


def _gemini() -> "object":
    from app.llm.gemini import GeminiProvider

    return GeminiProvider(api_key="AQ.test-key-not-real")


def _response(status: int, payload: dict) -> httpx.Response:
    return httpx.Response(
        status_code=status, json=payload, request=httpx.Request("POST", "http://x")
    )


async def test_404_becomes_model_unavailable() -> None:
    """gemini-2.5-flash really does 404 now — retired for new users."""
    provider = _gemini()
    response = _response(
        404, {"error": {"message": "models/gemini-2.5-flash is no longer available"}}
    )

    with pytest.raises(ModelUnavailableError):
        provider._raise_for_error(response, "gemini-2.5-flash")


async def test_429_about_billing_is_model_unavailable_not_rate_limit() -> None:
    """The important one.

    Pro models answer 429 'check your plan and billing' — that never clears,
    so treating it as a rate limit produces a retry loop that burns quota and
    still fails. It must surface as ModelUnavailableError.
    """
    provider = _gemini()
    response = _response(
        429,
        {
            "error": {
                "message": "You exceeded your current quota, please check your "
                "plan and billing details.",
                "details": [],
            }
        },
    )

    with pytest.raises(ModelUnavailableError):
        provider._raise_for_error(response, "gemini-3.1-pro-preview")


async def test_429_with_retry_info_is_a_real_rate_limit() -> None:
    provider = _gemini()
    response = _response(
        429,
        {
            "error": {
                "message": "Too many requests",
                "details": [
                    {
                        "@type": "type.googleapis.com/google.rpc.RetryInfo",
                        "retryDelay": "27s",
                    }
                ],
            }
        },
    )

    with pytest.raises(RateLimitError) as caught:
        provider._raise_for_error(response, "gemini-3.5-flash")

    # Google's own reset time must be honoured rather than guessed at.
    assert caught.value.retry_after == 27.0
    assert caught.value.daily is False


async def test_daily_quota_is_flagged_so_retrying_stops() -> None:
    provider = _gemini()
    response = _response(
        429,
        {
            "error": {
                "message": "Quota exceeded",
                "details": [
                    {
                        "@type": "type.googleapis.com/google.rpc.RetryInfo",
                        "retryDelay": "5s",
                    },
                    {
                        "@type": "type.googleapis.com/google.rpc.QuotaFailure",
                        "violations": [{"quotaId": "GenerateRequestsPerDayPerProject"}],
                    },
                ],
            }
        },
    )

    with pytest.raises(RateLimitError) as caught:
        provider._raise_for_error(response, "gemini-3.5-flash")

    assert caught.value.daily is True


async def test_busy_gemini_model_falls_back_without_showing_an_error(monkeypatch) -> None:
    """A capacity spike on Balanced must not stop a student from studying."""
    from app.core.config import settings

    provider = _gemini()
    monkeypatch.setattr(settings, "LLM_MODEL", "")
    monkeypatch.setattr(settings, "LLM_MODEL_BALANCED", "busy-model")
    monkeypatch.setattr(settings, "LLM_FALLBACK_MODELS", ("backup-model",))
    provider._models[ModelTier.BALANCED] = "busy-model"

    calls: list[tuple[str, int | None]] = []

    async def fake_post(path, body, model, *, retries=None):
        calls.append((model, retries))
        if model == "busy-model":
            raise RateLimitError("high demand", provider="gemini", model=model)
        return {
            "candidates": [{"content": {"parts": [{"text": "backup answer"}]}}],
            "usageMetadata": {"promptTokenCount": 3, "candidatesTokenCount": 2},
        }

    monkeypatch.setattr(provider, "_post", fake_post)

    response = await provider._complete(
        [Message(role="user", content="What is an electric field?")],
        tier=ModelTier.BALANCED,
        system=None,
        max_tokens=None,
        json_schema=None,
    )

    assert response.text == "backup answer"
    assert response.model == "backup-model"
    assert calls == [("busy-model", 0), ("backup-model", 0)]


async def test_api_key_never_appears_in_an_error_message() -> None:
    """A key leaked into a log or an HTTP response is a real incident."""
    provider = _gemini()
    response = _response(
        400, {"error": {"message": "bad request for key AQ.test-key-not-real"}}
    )

    with pytest.raises(Exception) as caught:
        provider._raise_for_error(response, "m")

    assert "AQ.test-key-not-real" not in str(caught.value)
    assert "<REDACTED>" in str(caught.value)


async def test_system_prompt_goes_to_system_instruction_not_a_turn() -> None:
    """Gemini follows systemInstruction far more reliably than a user turn."""
    provider = _gemini()

    body = provider._build_body(
        [Message(role="user", content="hi")],
        system="You are a maths tutor.",
        max_tokens=None,
    )

    assert body["systemInstruction"]["parts"][0]["text"] == "You are a maths tutor."
    assert len(body["contents"]) == 1


async def test_assistant_role_is_renamed_to_model() -> None:
    """Gemini rejects role='assistant'; it expects 'model'."""
    provider = _gemini()

    body = provider._build_body(
        [
            Message(role="user", content="q"),
            Message(role="assistant", content="a"),
        ],
        system=None,
        max_tokens=None,
    )

    assert [c["role"] for c in body["contents"]] == ["user", "model"]


async def test_missing_key_fails_fast_with_a_useful_message() -> None:
    from app.llm.gemini import GeminiProvider

    with pytest.raises(ConfigurationError, match="aistudio.google.com"):
        GeminiProvider(api_key="")


# ── status endpoint robustness ────────────────────────────────────────────


async def test_status_reports_misconfiguration_instead_of_crashing(
    client, monkeypatch
) -> None:
    """The endpoint whose job is explaining breakage must survive breakage.

    Returning a 500 with a stack trace here would be self-defeating — the
    caller wants to be told "no API key", not handed a traceback.
    """
    from app.core.config import settings

    reset_provider()
    monkeypatch.setattr(settings, "LLM_PROVIDER", "gemini")
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "")

    response = await client.get("/api/v1/llm/status")

    assert response.status_code == 200
    body = response.json()
    assert body["reachable"] is False
    assert "GEMINI_API_KEY" in body["detail"]
    assert body["key_fingerprint"] == "not set"
    reset_provider()


async def test_status_returns_configured_models_with_mock(client, monkeypatch) -> None:
    from app.core.config import settings

    reset_provider()
    monkeypatch.setattr(settings, "LLM_PROVIDER", "mock")

    body = (await client.get("/api/v1/llm/status")).json()

    assert body["reachable"] is True
    assert set(body["models"]) == {"fast", "balanced", "deep"}
    reset_provider()


async def test_stream_forwards_the_json_schema_to_the_provider() -> None:
    """Regression: `stream()` silently dropped `json_schema`.

    The symptom was subtle and expensive to chase — the non-streaming solve
    worked perfectly while the streaming one returned prose that failed schema
    validation, so it looked like a frontend bug.
    """
    from app.math.schema import SOLUTION_SCHEMA, Solution

    provider = MockProvider()

    chunks = [
        c async for c in provider.stream("solve the integral", json_schema=SOLUTION_SCHEMA)
    ]

    # The mock only emits JSON when it actually received a schema.
    Solution.model_validate_json("".join(chunks))
