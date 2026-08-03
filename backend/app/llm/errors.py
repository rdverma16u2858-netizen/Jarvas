"""
LLM error types.
═══════════════════════════════════════════════════════════════════════════

WHY A HIERARCHY INSTEAD OF ONE EXCEPTION
    Each failure needs a different response, and code that catches a single
    generic error cannot tell them apart:

    · RateLimitError      -> wait and retry; the request is fine
    · ModelUnavailableError -> retrying is pointless, fall back to another model
    · ProviderError    -> the provider broke; log it and surface a 502
    · LLMTimeoutError       -> retry once, then give up

    Catching `LLMError` still catches everything, so callers that genuinely do
    not care can stay simple.

WHY THEY ARE PROVIDER-NEUTRAL
    Nothing above this module should import a Gemini or Anthropic exception.
    Each provider translates its own errors into these, which is what makes
    swapping providers a config change rather than a rewrite.
"""

from __future__ import annotations


class LLMError(Exception):
    """Base class for every LLM failure. Catch this to catch them all."""

    def __init__(self, message: str, *, provider: str = "", model: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.provider = provider
        self.model = model

    def __str__(self) -> str:
        where = " ".join(filter(None, [self.provider, self.model]))
        return f"[{where}] {self.message}" if where else self.message


class RateLimitError(LLMError):
    """Quota or requests-per-minute limit hit (HTTP 429).

    `retry_after` is the provider's own advice on how long to wait, when it
    gives one. Honouring it is strictly better than a fixed backoff — Google
    returns the real reset time, so guessing either wastes time or gets
    throttled again immediately.
    """

    def __init__(
        self,
        message: str,
        *,
        provider: str = "",
        model: str = "",
        retry_after: float | None = None,
        daily: bool = False,
    ) -> None:
        super().__init__(message, provider=provider, model=model)
        self.retry_after = retry_after
        # A per-minute limit clears in seconds; a per-day quota does not clear
        # today. Retrying the second kind just burns the remaining budget.
        self.daily = daily


class ModelUnavailableError(LLMError):
    """The requested model does not exist, was retired, or this key cannot use it.

    Raised for HTTP 404 and for 429s that indicate the model has no free-tier
    quota at all. Retrying will never help — the caller should fall back to a
    different model or surface the problem.
    """


class ProviderError(LLMError):
    """The provider returned something unusable — 5xx, malformed JSON, empty body."""

    def __init__(
        self,
        message: str,
        *,
        provider: str = "",
        model: str = "",
        status_code: int | None = None,
    ) -> None:
        super().__init__(message, provider=provider, model=model)
        self.status_code = status_code


class LLMTimeoutError(LLMError):
    """The provider did not respond in time.

    Not unusual for this product: thinking-heavy models took 35s on a hard
    integral in testing, so timeouts are set generously and this is a real
    error rather than a sign something is broken.
    """


class ConfigurationError(LLMError):
    """The provider is misconfigured — missing API key, unknown provider name.

    Raised at startup wherever possible, so a missing key fails immediately
    instead of on the first student question.
    """
