"""
Provider selection.
═══════════════════════════════════════════════════════════════════════════

WHY A FACTORY
    This is the ONE place that maps `LLM_PROVIDER` in config to a concrete
    class. Every other module calls `get_provider()` and receives something
    that satisfies `LLMProvider` — none of them import GeminiProvider, so
    none of them break when the provider changes.

    That is the whole "swap models without changing code" promise, in about
    forty lines.

ADDING A PROVIDER LATER
    1. Write `anthropic.py` subclassing LLMProvider (implement _complete,
       _stream, check, model_for)
    2. Add one line to _BUILDERS below
    3. Set LLM_PROVIDER=anthropic in .env

    Nothing else in the codebase changes.
"""

from __future__ import annotations

from collections.abc import Callable

from app.core.config import settings
from app.core.logging import get_logger
from app.llm.base import LLMProvider
from app.llm.errors import ConfigurationError
from app.llm.mock import MockProvider

logger = get_logger(__name__)


def _build_gemini() -> LLMProvider:
    # Imported lazily so a missing key or an import error in one provider
    # cannot stop the app booting under a different provider.
    from app.llm.gemini import GeminiProvider

    return GeminiProvider()


_BUILDERS: dict[str, Callable[[], LLMProvider]] = {
    "gemini": _build_gemini,
    "mock": MockProvider,
    # Phase 11 (or whenever a paid key exists):
    # "anthropic": _build_anthropic,
    # "openai": _build_openai,
}

# Cached instance. Providers are stateless apart from the key and an httpx
# client, so one per process is right — and rebuilding on every request would
# re-read config and re-validate the key needlessly.
_instance: LLMProvider | None = None


def get_provider() -> LLMProvider:
    """Return the configured provider, building it on first use.

    Raises ConfigurationError for an unknown provider name or a missing key.
    Called from the app lifespan at startup so that failure happens on boot,
    not on the first student question.
    """
    global _instance
    if _instance is not None:
        return _instance

    name = settings.LLM_PROVIDER
    builder = _BUILDERS.get(name)

    if builder is None:
        raise ConfigurationError(
            f"LLM_PROVIDER={name!r} is not implemented. "
            f"Available: {', '.join(sorted(_BUILDERS))}"
        )

    _instance = builder()
    logger.info(
        "llm provider: %s (fast=%s balanced=%s deep=%s)",
        _instance.name,
        settings.LLM_MODEL_FAST,
        settings.LLM_MODEL_BALANCED,
        settings.LLM_MODEL_DEEP,
    )
    return _instance


def reset_provider() -> None:
    """Drop the cached instance.

    For tests that switch providers between cases — without this, the first
    test to call get_provider() would pin the choice for the whole session.
    """
    global _instance
    _instance = None
