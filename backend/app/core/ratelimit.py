"""
Rate limiting.
═══════════════════════════════════════════════════════════════════════════

WHAT THIS ACTUALLY PROTECTS AGAINST
    Not abuse. This is a single-user study tool, and the person using it is
    not attacking it.

    It protects the FREE-TIER QUOTA from accident. A component that
    re-renders in a loop, a double-tapped Generate on a 20-question set, a
    retry that fires on every keystroke — any of these can spend a day's
    Gemini allowance in under a minute, and the failure arrives as "the app
    stopped working" with no indication why.

    A local limit turns that into an immediate, legible 429 that names the
    limit and says when it resets. The quota survives; the mistake is visible.

    If the app is ever exposed publicly, the same mechanism is what stands
    between one bored visitor and the whole month's budget.

TWO TIERS, BECAUSE THE COSTS DIFFER BY ORDERS OF MAGNITUDE
    An LLM call costs a request against a quota measured in tens per day. A
    read of the question bank costs a SQLite query. Limiting them at the same
    rate would either throttle ordinary browsing or leave the expensive path
    effectively unlimited.

WHY A FIXED WINDOW
    A sliding window is more accurate at the seam: a fixed window allows up to
    2x the limit across a boundary. That matters when the limit IS the budget.
    Here the limit sits well under the provider's own, so the worst case is
    still comfortably inside quota — and a fixed window is two integers and an
    expiry rather than a sorted set per client.

    The provider's own 429 remains the real backstop, and Phase 1 already
    handles it.

ON TRUSTING X-Forwarded-For
    Behind a proxy the socket address is the proxy, so every client shares one
    bucket. The fix is to read the forwarded header — but that header is
    trivially spoofable by the client, so trusting it unconditionally means
    anyone can mint unlimited buckets and the limiter does nothing.

    So it is trusted ONLY when TRUST_PROXY_HEADER is set, which is a
    deployment decision made by whoever knows there is a proxy in front.
    Default off: a limiter that quietly does nothing is worse than none,
    because it is believed.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum

from fastapi import HTTPException, Request, status

from app.cache.client import cache
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class Tier(str, Enum):
    """What a request costs."""

    #: Anything that calls the model: solve, generate, review, ocr.
    LLM = "llm"
    #: Reads and writes that only touch the database.
    STANDARD = "standard"


@dataclass(frozen=True)
class Limit:
    requests: int
    window_seconds: int
    label: str


def _limits_for(tier: Tier) -> tuple[Limit, ...]:
    """The limits a tier must satisfy — ALL of them, not the first that matches.

    Two windows per tier on purpose. The per-minute limit catches a runaway
    loop within seconds; the daily limit is what actually protects the quota,
    and a loop slow enough to slip under the minute limit would still exhaust
    the day without it.
    """
    if tier is Tier.LLM:
        return (
            Limit(settings.RATE_LIMIT_LLM_PER_MINUTE, 60, "per minute"),
            Limit(settings.RATE_LIMIT_LLM_PER_DAY, 86_400, "per day"),
        )
    return (Limit(settings.RATE_LIMIT_STANDARD_PER_MINUTE, 60, "per minute"),)


def client_key(request: Request) -> str:
    """Identify the caller.

    See the module docstring: the forwarded header is honoured only when the
    deployment says there is a proxy, because otherwise it is client-supplied
    input being used as an identity.
    """
    if settings.TRUST_PROXY_HEADER:
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            # Left-most entry is the original client; the rest are proxies.
            return forwarded.split(",")[0].strip()

    return request.client.host if request.client else "unknown"


@dataclass
class Decision:
    allowed: bool
    limit: Limit | None = None
    retry_after: int = 0
    remaining: int = 0


async def check(identity: str, tier: Tier) -> Decision:
    """Count this request and decide whether it may proceed."""
    if not settings.RATE_LIMIT_ENABLED:
        return Decision(allowed=True)

    now = int(time.time())
    tightest_remaining = None

    for limit in _limits_for(tier):
        if limit.requests <= 0:
            continue  # 0 or negative disables this window

        # The window number is part of the key, so a window expires simply by
        # nobody writing to the old key any more.
        window = now // limit.window_seconds
        key = f"rl:{tier.value}:{identity}:{limit.window_seconds}:{window}"

        try:
            count = await cache.incr(key, ttl=limit.window_seconds)
        except Exception as exc:  # noqa: BLE001
            # A limiter that cannot count must not take the app down with it.
            # Failing OPEN is the deliberate choice: this exists to protect a
            # quota, and the provider's own 429 is still behind it, so a
            # broken Redis should not stop someone studying.
            logger.warning("rate limit check failed, allowing request: %s", exc)
            return Decision(allowed=True)

        if count > limit.requests:
            # Seconds until this window rolls over.
            retry_after = (window + 1) * limit.window_seconds - now
            return Decision(
                allowed=False,
                limit=limit,
                retry_after=max(1, retry_after),
            )

        remaining = limit.requests - count
        tightest_remaining = (
            remaining if tightest_remaining is None else min(tightest_remaining, remaining)
        )

    return Decision(allowed=True, remaining=tightest_remaining or 0)


def _dependency(tier: Tier):
    """Build a FastAPI dependency that enforces `tier`."""

    async def guard(request: Request) -> None:
        decision = await check(client_key(request), tier)
        if decision.allowed:
            return

        limit = decision.limit
        assert limit is not None  # set whenever allowed is False

        logger.info(
            "rate limited %s on %s (%s %s)",
            client_key(request),
            request.url.path,
            limit.requests,
            limit.label,
        )

        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            # Names the limit and when it lifts. "Too many requests" alone
            # leaves the user with nothing to do but retry blindly.
            detail=(
                f"Rate limit reached — {limit.requests} requests {limit.label}. "
                f"Try again in {decision.retry_after}s."
            ),
            headers={"Retry-After": str(decision.retry_after)},
        )

    return guard


#: Attach to any route that calls the model.
llm_rate_limit = _dependency(Tier.LLM)

#: Attach to ordinary database-backed routes.
standard_rate_limit = _dependency(Tier.STANDARD)
