"""
The provider-agnostic LLM interface.
═══════════════════════════════════════════════════════════════════════════

WHY THIS FILE IS THE MOST IMPORTANT ONE IN THE PROJECT
    Everything above it — the solver, question generation, mistake detection,
    quizzes — talks only to `LLMProvider`. None of them know which company's
    model is answering. That is what makes "swap the model without changing
    code" true rather than aspirational.

    Concretely: `GeminiProvider` and `MockProvider` are interchangeable, and
    an `AnthropicProvider` added later needs no change anywhere else.

THE TIER IDEA
    Measured on a hard integral with this project's own key:

        flash-lite   2.9s   correct
        3.5-flash   12.4s   correct
        3.6-flash   35.7s   correct

    All correct — but a 35-second wait for a question flash-lite answers in
    three is a bad trade, and a one-line classification job should never cost
    8,000 thinking tokens. So callers ask for a TIER, not a model name, and
    the mapping lives in config.

CACHING IS BUILT INTO THE BASE CLASS
    `complete()` is a template method: it checks the cache, and only on a miss
    calls `_complete()`, which is what providers actually implement. Every
    provider therefore gets caching for free and none can forget it.

    This matters more on a free tier than a paid one. Re-asking an identical
    question costs zero quota, and a hot-reloading dev server stops eating the
    daily allowance.
"""

from __future__ import annotations

import hashlib
import json
import time
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal

from app.cache.client import cache
from app.core.logging import get_logger

logger = get_logger(__name__)

Role = Literal["user", "assistant", "system"]


class ModelTier(str, Enum):
    """How much thinking a request deserves.

    Callers pick by the shape of the job, not by model name:

    FAST      classification, question generation, routine algebra.
              Optimised for latency; no extended thinking.
    BALANCED  the default for solving. Good accuracy at a usable wait.
    DEEP      proofs, olympiad problems, anything where being right matters
              more than being quick.
    """

    FAST = "fast"
    BALANCED = "balanced"
    DEEP = "deep"


@dataclass(frozen=True)
class Message:
    """One turn in a conversation.

    Frozen because messages are hashed for the cache key — a mutable message
    could be changed after hashing and silently poison the cache.
    """

    role: Role
    content: str


@dataclass
class LLMResponse:
    """What a provider returns.

    Token counts are optional because not every provider reports them, and
    `thinking_tokens` is specific to reasoning models. They are surfaced
    rather than hidden because on a free tier, knowing that one answer burned
    8,204 thinking tokens is the difference between an informed model choice
    and a mystery.
    """

    text: str
    model: str
    provider: str
    latency_ms: float
    input_tokens: int | None = None
    output_tokens: int | None = None
    thinking_tokens: int | None = None
    finish_reason: str | None = None
    # True when served from cache — no quota was used and latency is fake.
    cached: bool = False
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def total_tokens(self) -> int | None:
        parts = [self.input_tokens, self.output_tokens, self.thinking_tokens]
        known = [p for p in parts if p is not None]
        return sum(known) if known else None

    def json(self) -> Any:
        """Parse the response as JSON. Use with `json_schema=` requests.

        Raises ValueError with the offending text included, because a schema
        violation is far easier to debug when you can see what came back.
        """
        try:
            return json.loads(self.text)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Expected JSON from {self.model} but parsing failed: {exc}\n"
                f"--- response ---\n{self.text[:600]}"
            ) from exc


@dataclass
class ProviderStatus:
    """Result of a provider health check. Surfaced by /api/v1/llm/status."""

    provider: str
    reachable: bool
    models: dict[str, str]
    detail: str = ""
    key_fingerprint: str = ""


class LLMProvider(ABC):
    """What every provider must implement.

    Subclasses implement `_complete`, `_stream` and `check`. They must NOT
    override `complete` — that is the caching template method.
    """

    #: Short name used in logs, cache keys and health output.
    name: str = "base"

    # ── what subclasses implement ─────────────────────────────────────────

    @abstractmethod
    async def _complete(
        self,
        messages: Sequence[Message],
        *,
        tier: ModelTier,
        system: str | None,
        max_tokens: int | None,
        json_schema: dict[str, Any] | None,
    ) -> LLMResponse:
        """Do the actual call. Caching and key-building are already handled."""

    @abstractmethod
    def _stream(
        self,
        messages: Sequence[Message],
        *,
        tier: ModelTier,
        system: str | None,
        max_tokens: int | None,
        json_schema: dict[str, Any] | None = None,
    ) -> AsyncIterator[str]:
        """Yield text chunks as they arrive."""

    @abstractmethod
    async def check(self) -> ProviderStatus:
        """Verify the provider is reachable and configured. Never raises."""

    @abstractmethod
    def model_for(self, tier: ModelTier) -> str:
        """Resolve a tier to the concrete model id this provider will use."""

    # ── what callers use ──────────────────────────────────────────────────

    async def complete(
        self,
        messages: Sequence[Message] | str,
        *,
        tier: ModelTier = ModelTier.BALANCED,
        system: str | None = None,
        max_tokens: int | None = None,
        json_schema: dict[str, Any] | None = None,
        use_cache: bool = True,
        cache_ttl: int = 86_400,
    ) -> LLMResponse:
        """Send a request and return the response, using the cache when possible.

        Args:
            messages:    a conversation, or a bare string for a single question
            tier:        how much thinking the job deserves (see ModelTier)
            system:      system instruction, kept separate from the turns
            max_tokens:  output cap; None uses the provider default
            json_schema: constrain the reply to this JSON Schema
            use_cache:   set False when a fresh answer is required — e.g.
                         generating practice questions, where returning the
                         identical question every time defeats the purpose
            cache_ttl:   seconds to keep the entry. Default 24h: mathematics
                         does not change, so a correct solution stays correct.
        """
        # Ergonomic shortcut: complete("solve x^2=4") instead of building a list.
        if isinstance(messages, str):
            messages = [Message(role="user", content=messages)]

        model = self.model_for(tier)
        key = self._cache_key(messages, model, system, max_tokens, json_schema)

        if use_cache:
            hit = await cache.get(key)
            if hit is not None:
                logger.debug("llm cache hit  model=%s", model)
                return LLMResponse(**hit, cached=True)

        started = time.perf_counter()
        response = await self._complete(
            messages,
            tier=tier,
            system=system,
            max_tokens=max_tokens,
            json_schema=json_schema,
        )
        response.latency_ms = round((time.perf_counter() - started) * 1000, 1)

        logger.info(
            "llm call  model=%s  %.1fs  in=%s out=%s think=%s",
            response.model,
            response.latency_ms / 1000,
            response.input_tokens,
            response.output_tokens,
            response.thinking_tokens,
        )

        if use_cache:
            # `raw` is dropped before caching: it is large, provider-specific,
            # and only useful for debugging the live call.
            await cache.set(
                key,
                {
                    "text": response.text,
                    "model": response.model,
                    "provider": response.provider,
                    "latency_ms": response.latency_ms,
                    "input_tokens": response.input_tokens,
                    "output_tokens": response.output_tokens,
                    "thinking_tokens": response.thinking_tokens,
                    "finish_reason": response.finish_reason,
                },
                ttl=cache_ttl,
            )

        return response

    def stream(
        self,
        messages: Sequence[Message] | str,
        *,
        tier: ModelTier = ModelTier.BALANCED,
        system: str | None = None,
        max_tokens: int | None = None,
        json_schema: dict[str, Any] | None = None,
    ) -> AsyncIterator[str]:
        """Stream the reply as text chunks.

        Not cached. A stream is consumed once, and the point of streaming is
        that the user sees progress immediately — replaying a cached answer
        token by token would be theatre. Cache the assembled result upstream
        if a conversation needs it.

        Note this is `def`, not `async def`: it returns an async iterator, so
        callers write `async for chunk in provider.stream(...)` with no await
        on the call itself.
        """
        if isinstance(messages, str):
            messages = [Message(role="user", content=messages)]
        return self._stream(
            messages,
            tier=tier,
            system=system,
            max_tokens=max_tokens,
            json_schema=json_schema,
        )

    # ── internals ─────────────────────────────────────────────────────────

    def _cache_key(
        self,
        messages: Sequence[Message],
        model: str,
        system: str | None,
        max_tokens: int | None,
        json_schema: dict[str, Any] | None,
    ) -> str:
        """Build a key that changes whenever anything affecting the answer changes.

        The model id is included deliberately: flash-lite and 3.6-flash can
        give different answers to the same prompt, so a cached flash-lite reply
        must not be served to a request that asked for the deep tier.

        `sort_keys=True` matters — Python dict ordering is insertion-ordered,
        so the same schema built two different ways would otherwise serialise
        differently and miss the cache every time.
        """
        payload = json.dumps(
            {
                "provider": self.name,
                "model": model,
                "system": system,
                "max_tokens": max_tokens,
                "schema": json_schema,
                "messages": [{"role": m.role, "content": m.content} for m in messages],
            },
            sort_keys=True,
            ensure_ascii=False,
        )
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]
        return f"llm:{self.name}:{digest}"
