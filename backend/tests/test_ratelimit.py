"""
Tests for rate limiting.
═══════════════════════════════════════════════════════════════════════════

WHAT MATTERS HERE
    · The counter must be atomic. A limiter built on get-then-set undercounts
      exactly when it is needed most — under a burst.
    · The window must NOT slide forward on every hit, or a steady stream of
      requests extends its own window forever and is never limited.
    · A broken cache must fail OPEN. This protects a quota; it is not a
      security control, and it must not stop someone studying because Redis
      restarted.
    · X-Forwarded-For must be ignored unless the deployment opts in — it is
      client-supplied, and trusting it by default means anyone can mint
      unlimited buckets while the limiter appears to work.
"""

import asyncio

import pytest
from httpx import AsyncClient

from app.cache.client import InMemoryCache, cache
from app.core.config import settings
from app.core.ratelimit import Tier, check, client_key
from app.llm.factory import reset_provider

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _mock_provider_and_clean_counters(monkeypatch):
    reset_provider()
    monkeypatch.setattr(settings, "LLM_PROVIDER", "mock")
    monkeypatch.setattr(settings, "RATE_LIMIT_ENABLED", True)
    # A fresh backend per test, so one test's counters cannot limit the next.
    monkeypatch.setattr(cache, "_backend", InMemoryCache())
    yield
    reset_provider()


# ── the counter ───────────────────────────────────────────────────────────


async def test_the_counter_is_atomic_under_a_burst() -> None:
    """Twenty concurrent increments must produce twenty, not fewer.

    A limiter built on get-then-set loses increments precisely when requests
    arrive together — which is the only time it matters.
    """
    backend = InMemoryCache()

    counts = await asyncio.gather(*(backend.incr("k", ttl=60) for _ in range(20)))

    assert sorted(counts) == list(range(1, 21))


async def test_the_window_does_not_slide_forward_on_every_hit() -> None:
    """The TTL is set on creation only.

    Refreshing it per request would let a steady stream push its own window
    ahead of itself indefinitely and never reset.
    """
    backend = InMemoryCache()

    await backend.incr("k", ttl=60)
    first_expiry = backend._store["k"][1]
    await backend.incr("k", ttl=60)
    second_expiry = backend._store["k"][1]

    assert first_expiry == second_expiry


async def test_the_counter_expires() -> None:
    backend = InMemoryCache()

    await backend.incr("k", ttl=1)
    backend._store["k"] = (backend._store["k"][0], 0)  # force expiry

    assert await backend.incr("k", ttl=1) == 1, "an expired window starts again"


# ── the decision ──────────────────────────────────────────────────────────


async def test_requests_are_allowed_up_to_the_limit(monkeypatch) -> None:
    monkeypatch.setattr(settings, "RATE_LIMIT_LLM_PER_MINUTE", 3)
    monkeypatch.setattr(settings, "RATE_LIMIT_LLM_PER_DAY", 1000)

    verdicts = [(await check("someone", Tier.LLM)).allowed for _ in range(4)]

    assert verdicts == [True, True, True, False]


async def test_a_refusal_says_when_it_lifts(monkeypatch) -> None:
    """ "Too many requests" alone leaves the user retrying blindly."""
    monkeypatch.setattr(settings, "RATE_LIMIT_LLM_PER_MINUTE", 1)

    await check("someone", Tier.LLM)
    decision = await check("someone", Tier.LLM)

    assert decision.allowed is False
    assert 0 < decision.retry_after <= 60
    assert decision.limit is not None
    assert decision.limit.label == "per minute"


async def test_the_daily_window_catches_a_slow_loop(monkeypatch) -> None:
    """A loop slow enough to stay under the minute limit would still exhaust
    the quota without the second window."""
    monkeypatch.setattr(settings, "RATE_LIMIT_LLM_PER_MINUTE", 1000)
    monkeypatch.setattr(settings, "RATE_LIMIT_LLM_PER_DAY", 2)

    verdicts = [(await check("someone", Tier.LLM)).allowed for _ in range(3)]

    assert verdicts == [True, True, False]
    assert (await check("someone", Tier.LLM)).limit.label == "per day"


async def test_tiers_are_counted_separately(monkeypatch) -> None:
    """A database read must not consume the model-call budget."""
    monkeypatch.setattr(settings, "RATE_LIMIT_LLM_PER_MINUTE", 1)
    monkeypatch.setattr(settings, "RATE_LIMIT_STANDARD_PER_MINUTE", 100)

    await check("someone", Tier.LLM)

    assert (await check("someone", Tier.LLM)).allowed is False
    assert (await check("someone", Tier.STANDARD)).allowed is True


async def test_callers_are_counted_separately(monkeypatch) -> None:
    monkeypatch.setattr(settings, "RATE_LIMIT_LLM_PER_MINUTE", 1)

    await check("first", Tier.LLM)

    assert (await check("first", Tier.LLM)).allowed is False
    assert (await check("second", Tier.LLM)).allowed is True


async def test_a_zero_limit_disables_that_window(monkeypatch) -> None:
    monkeypatch.setattr(settings, "RATE_LIMIT_LLM_PER_MINUTE", 0)
    monkeypatch.setattr(settings, "RATE_LIMIT_LLM_PER_DAY", 0)

    verdicts = [(await check("someone", Tier.LLM)).allowed for _ in range(50)]

    assert all(verdicts)


async def test_disabling_the_limiter_allows_everything(monkeypatch) -> None:
    monkeypatch.setattr(settings, "RATE_LIMIT_ENABLED", False)
    monkeypatch.setattr(settings, "RATE_LIMIT_LLM_PER_MINUTE", 1)

    verdicts = [(await check("someone", Tier.LLM)).allowed for _ in range(10)]

    assert all(verdicts)


# ── failure modes ─────────────────────────────────────────────────────────


async def test_a_broken_cache_fails_open(monkeypatch) -> None:
    """This guards a quota, not a door. The provider's own 429 is still
    behind it, so a Redis restart must not stop someone studying."""

    class Broken(InMemoryCache):
        async def incr(self, key: str, ttl: int) -> int:
            raise RuntimeError("redis is down")

    monkeypatch.setattr(cache, "_backend", Broken())
    monkeypatch.setattr(settings, "RATE_LIMIT_LLM_PER_MINUTE", 1)

    assert (await check("someone", Tier.LLM)).allowed is True
    assert (await check("someone", Tier.LLM)).allowed is True


# ── identifying the caller ────────────────────────────────────────────────


def a_request(host: str = "10.0.0.1", forwarded: str | None = None):
    """A stand-in Request carrying just what client_key reads."""

    class Client:
        def __init__(self, h: str) -> None:
            self.host = h

    class Request:
        def __init__(self) -> None:
            self.client = Client(host)
            self.headers = {"x-forwarded-for": forwarded} if forwarded else {}

    return Request()


async def test_the_forwarded_header_is_ignored_by_default(monkeypatch) -> None:
    """It is client-supplied. Trusting it unconditionally lets anyone mint
    unlimited buckets while the limiter appears to be working."""
    monkeypatch.setattr(settings, "TRUST_PROXY_HEADER", False)

    key = client_key(a_request(host="10.0.0.1", forwarded="1.2.3.4"))

    assert key == "10.0.0.1"


async def test_the_forwarded_header_is_used_when_the_deployment_opts_in(
    monkeypatch,
) -> None:
    """Behind a proxy the socket address is the proxy, so every client would
    otherwise share one bucket."""
    monkeypatch.setattr(settings, "TRUST_PROXY_HEADER", True)

    key = client_key(a_request(host="10.0.0.1", forwarded="1.2.3.4, 10.0.0.9"))

    assert key == "1.2.3.4", "the left-most entry is the original client"


# ── wired to the routes that cost money ───────────────────────────────────


async def test_the_solve_endpoint_is_limited(client: AsyncClient, monkeypatch) -> None:
    monkeypatch.setattr(settings, "RATE_LIMIT_LLM_PER_MINUTE", 2)

    codes = []
    for _ in range(3):
        response = await client.post(
            "/api/v1/solve", json={"problem": "solve x^2 = 4", "tier": "fast"}
        )
        codes.append(response.status_code)

    assert codes[:2] == [200, 200]
    assert codes[2] == 429


async def test_a_limited_response_carries_retry_after(
    client: AsyncClient, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "RATE_LIMIT_LLM_PER_MINUTE", 1)

    await client.post("/api/v1/solve", json={"problem": "solve x^2 = 4", "tier": "fast"})
    blocked = await client.post(
        "/api/v1/solve", json={"problem": "solve x^2 = 4", "tier": "fast"}
    )

    assert blocked.status_code == 429
    assert "Retry-After" in blocked.headers
    assert "per minute" in blocked.json()["detail"]


async def test_every_model_calling_route_is_guarded() -> None:
    """A new expensive endpoint added without the guard is the way this
    protection quietly stops covering the thing it exists for."""
    from app.core.ratelimit import llm_rate_limit
    from app.main import app

    expensive = {
        ("POST", "/api/v1/solve"),
        ("POST", "/api/v1/solve/stream"),
        ("POST", "/api/v1/generate"),
        ("POST", "/api/v1/review"),
        ("POST", "/api/v1/ocr"),
    }

    guarded = set()
    for route in app.routes:
        dependencies = getattr(route, "dependencies", [])
        if any(d.dependency is llm_rate_limit for d in dependencies):
            for method in getattr(route, "methods", set()):
                guarded.add((method, route.path))

    assert expensive <= guarded, f"unguarded: {expensive - guarded}"


async def test_reads_are_not_limited_at_the_model_rate(
    client: AsyncClient, monkeypatch
) -> None:
    """Browsing the question bank costs a local query and must not consume the
    model budget."""
    monkeypatch.setattr(settings, "RATE_LIMIT_LLM_PER_MINUTE", 1)

    codes = [(await client.get("/api/v1/generate/questions")).status_code for _ in range(5)]

    assert codes == [200] * 5
