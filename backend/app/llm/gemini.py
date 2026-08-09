"""
Google Gemini provider.
═══════════════════════════════════════════════════════════════════════════

WHY RAW HTTP INSTEAD OF THE OFFICIAL SDK
    The REST surface is small and stable, and going direct means: no SDK
    version churn to track, exact control over retry and cache behaviour, and
    an interface shaped by THIS project rather than by one vendor's client.
    The abstraction in base.py is only honest if it is not secretly a thin
    wrapper around one SDK's idioms.

WHAT WAS LEARNED BY TESTING AGAINST A REAL FREE-TIER KEY
    · Pro models return 429 immediately — they have no free-tier quota, so
      treating that as "retry later" would loop forever. It is translated to
      ModelUnavailableError instead.
    · `gemini-2.5-flash` returns 404 "no longer available to new users" —
      model ids retire, which is why none are hardcoded outside config.
    · Newer API keys start with `AQ.` rather than the classic `AIzaSy`, so
      any validation that assumes the old prefix rejects a working key.
    · Thinking tokens dominate: 8,204 thinking vs 673 output on one problem.
      They are reported separately so cost is visible.
"""

from __future__ import annotations

import json
import random
from collections.abc import AsyncIterator, Sequence
from typing import Any

import httpx

from app.core.config import settings
from app.core.logging import get_logger
from app.llm.base import (
    LLMProvider,
    LLMResponse,
    Message,
    ModelTier,
    ProviderStatus,
)
from app.llm.errors import (
    ConfigurationError,
    LLMTimeoutError,
    ModelUnavailableError,
    ProviderError,
    RateLimitError,
)

logger = get_logger(__name__)

BASE_URL = "https://generativelanguage.googleapis.com/v1beta"


class GeminiProvider(LLMProvider):
    """Talks to the Gemini API over REST."""

    name = "gemini"

    def __init__(self, api_key: str | None = None) -> None:
        self._key = api_key if api_key is not None else settings.GEMINI_API_KEY
        if not self._key:
            raise ConfigurationError(
                "GEMINI_API_KEY is empty. Add it to .env — free key at "
                "https://aistudio.google.com/apikey",
                provider=self.name,
            )

        self._models = {
            ModelTier.FAST: settings.LLM_MODEL_FAST,
            ModelTier.BALANCED: settings.LLM_MODEL_BALANCED,
            ModelTier.DEEP: settings.LLM_MODEL_DEEP,
        }

    # ── tier resolution ───────────────────────────────────────────────────

    def model_for(self, tier: ModelTier) -> str:
        # LLM_MODEL pins every tier to one model when set — useful for A/B
        # testing a single model across the whole app without editing tiers.
        return settings.LLM_MODEL or self._models[tier]

    def _models_for_request(self, tier: ModelTier) -> tuple[str, ...]:
        """Return the selected model followed by unique configured backups."""
        candidates = (self.model_for(tier), *settings.LLM_FALLBACK_MODELS)
        return tuple(dict.fromkeys(model.strip() for model in candidates if model.strip()))

    # ── request building ──────────────────────────────────────────────────

    def _build_body(
        self,
        messages: Sequence[Message],
        *,
        system: str | None,
        max_tokens: int | None,
        json_schema: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Translate the neutral Message list into Gemini's wire format.

        Two mappings worth knowing:

        · Gemini calls the assistant role "model", not "assistant".
        · A system prompt is NOT a message with role=system. It goes in a
          separate `systemInstruction` field; sending it as a turn makes the
          model treat it as user text and follow it far less reliably.
        """
        contents: list[dict[str, Any]] = []
        system_parts: list[str] = [system] if system else []

        for m in messages:
            if m.role == "system":
                # Fold stray system turns into the system instruction rather
                # than silently dropping them.
                system_parts.append(m.content)
                continue

            # Images BEFORE the text. Gemini's guidance for a single image is
            # to put it first, and it reads noticeably better when the
            # instruction follows the thing it refers to.
            parts: list[dict[str, Any]] = [
                {"inlineData": {"mimeType": image.mime_type, "data": image.data}}
                for image in m.images
            ]
            if m.content:
                parts.append({"text": m.content})

            contents.append(
                {
                    "role": "model" if m.role == "assistant" else "user",
                    "parts": parts,
                }
            )

        body: dict[str, Any] = {"contents": contents}

        if system_parts:
            body["systemInstruction"] = {"parts": [{"text": "\n\n".join(system_parts)}]}

        generation: dict[str, Any] = {}
        if max_tokens is not None:
            generation["maxOutputTokens"] = max_tokens
        if json_schema is not None:
            # Constrained decoding: the model is forced to emit JSON matching
            # the schema, which is what Phase 2's 10-part solution object needs.
            generation["responseMimeType"] = "application/json"
            generation["responseSchema"] = json_schema
        if generation:
            body["generationConfig"] = generation

        return body

    # ── error translation ─────────────────────────────────────────────────

    def _raise_for_error(self, response: httpx.Response, model: str) -> None:
        """Turn an HTTP error into the right provider-neutral exception.

        The 429 branch is the subtle one. Google uses 429 for two very
        different situations:

          a) "you are going too fast"        -> wait and retry, will succeed
          b) "this model has no free quota"  -> retrying can never succeed

        Both are 429. Telling them apart matters: treating (b) as (a) produces
        a retry loop that burns the daily allowance and still fails.
        """
        status = response.status_code
        try:
            payload = response.json()
            error = payload.get("error", {})
            message = error.get("message", response.text[:300])
            details = error.get("details", [])
        except Exception:
            message = response.text[:300]
            details = []

        # Never let the URL (it carries the key) reach a log or a client.
        message = message.replace(self._key, "<REDACTED>")

        if status == 404:
            raise ModelUnavailableError(message, provider=self.name, model=model)

        if status == 429:
            retry_after: float | None = None
            daily = False
            for detail in details:
                dtype = detail.get("@type", "")
                if dtype.endswith("RetryInfo"):
                    raw = str(detail.get("retryDelay", ""))
                    if raw.endswith("s"):
                        try:
                            retry_after = float(raw[:-1])
                        except ValueError:
                            pass
                if dtype.endswith("QuotaFailure"):
                    for violation in detail.get("violations", []):
                        quota_id = str(violation.get("quotaId", "")).lower()
                        if "perday" in quota_id or "per_day" in quota_id:
                            daily = True

            lowered = message.lower()
            # No RetryInfo AND wording about plan/billing means this model is
            # simply not on the free tier — that is (b) above.
            if retry_after is None and ("billing" in lowered or "plan and billing" in lowered):
                raise ModelUnavailableError(
                    f"{model} has no free-tier quota for this key ({message})",
                    provider=self.name,
                    model=model,
                )

            raise RateLimitError(
                message,
                provider=self.name,
                model=model,
                retry_after=retry_after,
                daily=daily,
            )

        if status in (401, 403):
            raise ConfigurationError(
                f"Gemini rejected the API key ({status}): {message}",
                provider=self.name,
                model=model,
            )

        raise ProviderError(message, provider=self.name, model=model, status_code=status)

    # ── the call, with retry ──────────────────────────────────────────────

    async def _post(
        self,
        path: str,
        body: dict[str, Any],
        model: str,
        *,
        retries: int | None = None,
    ) -> dict[str, Any]:
        """POST with exponential backoff on rate limits and 5xx.

        Google's own `retryDelay` is preferred over a computed backoff — it is
        the real reset time, so guessing either wastes seconds or gets
        throttled again immediately. Jitter is added so several concurrent
        requests do not all retry on the same tick.
        """
        url = f"{BASE_URL}/{path}"
        last: Exception | None = None

        retry_limit = settings.LLM_MAX_RETRIES if retries is None else retries

        for attempt in range(retry_limit + 1):
            try:
                async with httpx.AsyncClient(timeout=settings.LLM_TIMEOUT) as client:
                    response = await client.post(
                        url,
                        json=body,
                        headers={
                            "Content-Type": "application/json",
                            # Header auth, not ?key= in the query string: URLs
                            # end up in logs and proxy traces, headers do not.
                            "x-goog-api-key": self._key,
                        },
                    )
                if response.status_code >= 400:
                    self._raise_for_error(response, model)
                return response.json()

            except (RateLimitError, ProviderError) as exc:
                last = exc
                if isinstance(exc, RateLimitError) and exc.daily:
                    raise  # a daily quota will not clear by waiting
                if attempt >= retry_limit:
                    raise

                wait = getattr(exc, "retry_after", None) or (2.0**attempt)
                wait += random.uniform(0, 0.5)
                logger.warning(
                    "gemini %s — retry %d/%d in %.1fs",
                    type(exc).__name__,
                    attempt + 1,
                    retry_limit,
                    wait,
                )
                import asyncio

                await asyncio.sleep(wait)

            except httpx.TimeoutException as exc:
                last = exc
                if attempt >= retry_limit:
                    raise LLMTimeoutError(
                        f"no response in {settings.LLM_TIMEOUT}s",
                        provider=self.name,
                        model=model,
                    ) from exc
            except httpx.HTTPError as exc:
                raise ProviderError(
                    f"network error: {exc}", provider=self.name, model=model
                ) from exc

        raise ProviderError(f"exhausted retries: {last}", provider=self.name, model=model)

    # ── response parsing ──────────────────────────────────────────────────

    @staticmethod
    def _extract_text(payload: dict[str, Any], model: str) -> tuple[str, str | None]:
        """Pull the text out of a Gemini response, or explain why there is none.

        An empty-but-successful response is a real case: the model can stop on
        a safety filter, or spend its whole output budget on thinking and have
        nothing left. Both look like `candidates[0]` with no text parts, and
        both deserve a clear message rather than an IndexError.
        """
        candidates = payload.get("candidates") or []
        if not candidates:
            feedback = payload.get("promptFeedback", {})
            blocked = feedback.get("blockReason")
            raise ProviderError(
                f"no candidates returned{f' (blocked: {blocked})' if blocked else ''}",
                model=model,
            )

        candidate = candidates[0]
        finish = candidate.get("finishReason")
        parts = candidate.get("content", {}).get("parts", []) or []
        text = "".join(p.get("text", "") for p in parts)

        if not text:
            if finish == "MAX_TOKENS":
                raise ProviderError(
                    "hit the output limit before writing an answer — raise "
                    "max_tokens (thinking tokens count toward it)",
                    model=model,
                )
            raise ProviderError(f"empty response (finishReason={finish})", model=model)

        return text, finish

    # ── LLMProvider implementation ────────────────────────────────────────

    async def _complete(
        self,
        messages: Sequence[Message],
        *,
        tier: ModelTier,
        system: str | None,
        max_tokens: int | None,
        json_schema: dict[str, Any] | None,
    ) -> LLMResponse:
        body = self._build_body(
            messages, system=system, max_tokens=max_tokens, json_schema=json_schema
        )
        models = self._models_for_request(tier)
        last_error: RateLimitError | ModelUnavailableError | None = None

        for index, model in enumerate(models):
            try:
                # A different available model is more helpful than repeatedly
                # retrying one overloaded model. With no backup configured,
                # preserve the regular exponential-backoff behaviour.
                # Retrying a busy FAST free-tier model inside one request can
                # outlive Render's proxy deadline and turn into a browser
                # network error. Return a timely, actionable API response.
                retries = 0 if tier is ModelTier.FAST or len(models) > 1 else None
                payload = await self._post(
                    f"models/{model}:generateContent", body, model, retries=retries
                )
                break
            except (RateLimitError, ModelUnavailableError) as exc:
                last_error = exc
                if index == len(models) - 1:
                    raise
                logger.warning(
                    "gemini model unavailable; falling back from=%s to=%s reason=%s",
                    model,
                    models[index + 1],
                    type(exc).__name__,
                )
        else:  # Defensive: the loop always returns or raises above.
            raise last_error or ProviderError(
                "no Gemini models configured", provider=self.name
            )

        text, finish = self._extract_text(payload, model)
        usage = payload.get("usageMetadata", {})

        return LLMResponse(
            text=text,
            model=model,
            provider=self.name,
            latency_ms=0.0,  # base.complete() measures and overwrites this
            input_tokens=usage.get("promptTokenCount"),
            output_tokens=usage.get("candidatesTokenCount"),
            thinking_tokens=usage.get("thoughtsTokenCount"),
            finish_reason=finish,
            raw=payload,
        )

    async def _stream(
        self,
        messages: Sequence[Message],
        *,
        tier: ModelTier,
        system: str | None,
        max_tokens: int | None,
        json_schema: dict[str, Any] | None = None,
    ) -> AsyncIterator[str]:
        """Stream chunks via server-sent events.

        Streaming matters here more than in most products: a deep-tier model
        took 35 seconds on one integral. Streamed, the student watches an
        explanation appear; unstreamed, they stare at a spinner and assume it
        has hung.

        A backup model is tried only when Gemini fails before it sends text.
        Once bytes have been sent to the client, restarting would duplicate
        the visible answer.
        """
        body = self._build_body(
            messages, system=system, max_tokens=max_tokens, json_schema=json_schema
        )
        models = self._models_for_request(tier)
        last_error: RateLimitError | ModelUnavailableError | None = None

        for index, model in enumerate(models):
            url = f"{BASE_URL}/models/{model}:streamGenerateContent?alt=sse"
            emitted_text = False

            try:
                async with httpx.AsyncClient(timeout=settings.LLM_TIMEOUT) as client:
                    async with client.stream(
                        "POST",
                        url,
                        json=body,
                        headers={
                            "Content-Type": "application/json",
                            "x-goog-api-key": self._key,
                        },
                    ) as response:
                        if response.status_code >= 400:
                            await response.aread()
                            self._raise_for_error(response, model)

                        async for line in response.aiter_lines():
                            if not line.startswith("data:"):
                                continue
                            raw = line[5:].strip()
                            if not raw or raw == "[DONE]":
                                continue
                            try:
                                chunk = json.loads(raw)
                            except json.JSONDecodeError:
                                continue  # keep-alive or partial frame

                            for candidate in chunk.get("candidates", []):
                                for part in candidate.get("content", {}).get("parts", []):
                                    if text := part.get("text"):
                                        emitted_text = True
                                        yield text
                return

            except (RateLimitError, ModelUnavailableError) as exc:
                # A restart after text reaches the browser would duplicate the
                # answer, so only pre-response errors are safe to retry.
                if emitted_text or index == len(models) - 1:
                    raise
                last_error = exc
                logger.warning(
                    "gemini stream unavailable; falling back from=%s to=%s reason=%s",
                    model,
                    models[index + 1],
                    type(exc).__name__,
                )
            except httpx.TimeoutException as exc:
                raise LLMTimeoutError(
                    f"stream stalled after {settings.LLM_TIMEOUT}s",
                    provider=self.name,
                    model=model,
                ) from exc
            except httpx.HTTPError as exc:
                raise ProviderError(
                    f"stream failed: {exc}", provider=self.name, model=model
                ) from exc

        raise last_error or ProviderError("no Gemini models configured", provider=self.name)

    async def check(self) -> ProviderStatus:
        """List models to prove the key is valid and the service reachable.

        Uses the models endpoint rather than a generateContent call so the
        health check costs no generation quota — important when the whole
        daily budget is a few hundred requests.
        """
        fingerprint = f"{self._key[:4]}...{self._key[-4:]}" if len(self._key) > 12 else "set"
        models = {t.value: self.model_for(t) for t in ModelTier}

        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.get(
                    f"{BASE_URL}/models",
                    headers={"x-goog-api-key": self._key},
                    params={"pageSize": 100},
                )
            if response.status_code >= 400:
                self._raise_for_error(response, "models.list")

            available = {
                m["name"].removeprefix("models/")
                for m in response.json().get("models", [])
                if "generateContent" in m.get("supportedGenerationMethods", [])
            }
            # Catch a retired or misspelled model id at health-check time
            # rather than when a student asks a question.
            missing = [m for m in models.values() if m not in available]
            detail = (
                f"{len(available)} models available"
                if not missing
                else f"CONFIGURED MODEL NOT AVAILABLE: {', '.join(missing)}"
            )
            return ProviderStatus(
                provider=self.name,
                reachable=not missing,
                models=models,
                detail=detail,
                key_fingerprint=fingerprint,
            )

        except Exception as exc:  # noqa: BLE001 — a health check must not raise
            return ProviderStatus(
                provider=self.name,
                reachable=False,
                models=models,
                detail=str(exc)[:200],
                key_fingerprint=fingerprint,
            )
