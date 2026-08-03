"""
Mock provider — no network, no key, no quota.
═══════════════════════════════════════════════════════════════════════════

WHY THIS IS NOT A TOY
    On a free tier this is what makes the project workable day to day:

    · Tests never touch the network. The suite stays fast and deterministic,
      and CI needs no secret.
    · `uvicorn --reload` restarts on every save. Without a mock, an afternoon
      of editing would spend the daily request allowance on nothing.
    · A wrong answer from a real model and a broken code path look identical.
      With fixed responses, any failure is definitely the code.

    Switch with one line in .env:  LLM_PROVIDER=mock

HOW IT ANSWERS
    A few known problems return real, correct mathematics — enough for the
    SymPy verification layer in Phase 2 to be exercised end to end, including
    the case where verification must FAIL (see `wrong integral` below).
    Anything unrecognised gets a generic templated answer.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Sequence
from typing import Any

from app.llm.base import (
    LLMProvider,
    LLMResponse,
    Message,
    ModelTier,
    ProviderStatus,
)

# Substring -> canned reply. Matched case-insensitively against the last user
# message. Kept deliberately small; this is a stub, not a maths engine.
CANNED: dict[str, str] = {
    "2+2": "4",
    "derivative of x^2": (
        "Using the power rule, d/dx(x^n) = n·x^(n-1).\n\n"
        "With n = 2:  d/dx(x^2) = 2x\n\n"
        "FINAL: 2x"
    ),
    "integral of ln(1+x)/(1+x^2)": (
        "Substitute x = tan(θ), so dx = sec²(θ)dθ and the limits become 0 to π/4.\n\n"
        "I = ∫₀^{π/4} ln(1 + tan θ) dθ\n\n"
        "Using the symmetry θ → π/4 − θ and the identity\n"
        "(1 + tan θ)(1 + tan(π/4 − θ)) = 2, we get 2I = (π/4)·ln 2.\n\n"
        "FINAL: (π · ln 2) / 8 ≈ 0.2721982613"
    ),
    "solve x^2 = 4": ("x² = 4\nx² − 4 = 0\n(x − 2)(x + 2) = 0\n\nFINAL: x = 2 or x = −2"),
    # Deliberately WRONG. Phase 2 needs a case where SymPy catches the model,
    # and a verifier that has never seen a failure is a verifier nobody trusts.
    "wrong integral": ("The integral of x² is x²/2.\n\nFINAL: x^2/2 + C"),
}

GENERIC = (
    "[mock provider] No canned answer for this question.\n\n"
    "Set LLM_PROVIDER=gemini in .env for real answers.\n\n"
    "FINAL: (mock response)"
)


class MockProvider(LLMProvider):
    """Deterministic stand-in for a real provider."""

    name = "mock"

    def __init__(self, *, latency: float = 0.0) -> None:
        # Simulated delay. Leave at 0 for tests; set it to ~2s by hand when
        # checking that the UI's loading states actually appear.
        self._latency = latency

    def model_for(self, tier: ModelTier) -> str:
        return f"mock-{tier.value}"

    @staticmethod
    def _answer(messages: Sequence[Message]) -> str:
        last = next((m.content for m in reversed(messages) if m.role == "user"), "").lower()
        for needle, reply in CANNED.items():
            if needle.lower() in last:
                return reply
        return GENERIC

    async def _complete(
        self,
        messages: Sequence[Message],
        *,
        tier: ModelTier,
        system: str | None,
        max_tokens: int | None,
        json_schema: dict[str, Any] | None,
    ) -> LLMResponse:
        if self._latency:
            await asyncio.sleep(self._latency)

        text = self._answer(messages)

        # When a schema is requested, return a COMPLETE, schema-valid solution.
        # Returning a stub here would mean the mock works and the real provider
        # breaks — exactly the bug a mock must not introduce.
        if json_schema is not None:
            text = self._solution_json(messages)

        return LLMResponse(
            text=text,
            model=self.model_for(tier),
            provider=self.name,
            latency_ms=0.0,
            # Rough but non-None, so token-accounting code paths are exercised.
            input_tokens=sum(len(m.content) for m in messages) // 4,
            output_tokens=len(text) // 4,
            thinking_tokens=0,
            finish_reason="STOP",
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
        """Yield in small pieces so streaming UIs have something to render.

        When a schema is requested the mock streams the same JSON its
        non-streaming path returns, so the frontend's incremental JSON
        handling is exercised without a network call.
        """
        text = (
            self._solution_json(messages)
            if json_schema is not None
            else self._answer(messages)
        )
        for word in text.split(" "):
            if self._latency:
                await asyncio.sleep(self._latency / 40)
            yield word + " "

    @staticmethod
    def _solution_json(messages: Sequence[Message]) -> str:
        """A complete ten-part solution, matching the Phase 2 schema.

        Two variants, chosen by keyword. The WRONG one exists so the frontend's
        "refuted" state can be developed and demonstrated without waiting for a
        real model to make a real mistake — a state that is otherwise almost
        impossible to reproduce on demand, and therefore the one most likely to
        ship broken.
        """
        last = next((m.content for m in reversed(messages) if m.role == "user"), "").lower()
        wrong = "wrong" in last

        return json.dumps(
            {
                "topic": "integral_calculus",
                "steps": [
                    {
                        "number": 1,
                        "action": "Substitute $x = \\tan\\theta$",
                        "expression": "I = \\int_0^{\\pi/4} \\ln(1 + \\tan\\theta)\\,d\\theta",
                        "justification": (
                            "With $x=\\tan\\theta$ we get $dx=\\sec^2\\theta\\,d\\theta$, "
                            "and $1+x^2=\\sec^2\\theta$, so the denominator cancels."
                        ),
                    },
                    {
                        "number": 2,
                        "action": "Apply the reflection $\\theta \\to \\pi/4 - \\theta$",
                        "expression": "2I = \\int_0^{\\pi/4} \\ln 2 \\, d\\theta",
                        "justification": (
                            "$(1+\\tan\\theta)(1+\\tan(\\pi/4-\\theta)) = 2$, so adding the "
                            "integral to its reflection collapses the logarithm to $\\ln 2$."
                        ),
                    },
                    {
                        "number": 3,
                        "action": "Evaluate",
                        "expression": "I = \\frac{\\pi \\ln 2}{8}",
                        "justification": "The integrand is now constant over an interval of length $\\pi/4$.",
                    },
                ],
                "formulas_used": [
                    "\\tan(\\pi/4 - \\theta) = \\frac{1-\\tan\\theta}{1+\\tan\\theta}",
                    "\\int_a^b f(x)\\,dx = \\int_a^b f(a+b-x)\\,dx",
                ],
                "final_answer": (
                    "x^2/2 + C  [mock: deliberately wrong]"
                    if wrong
                    else "\\frac{\\pi \\ln 2}{8} \\approx 0.2721982613"
                ),
                "answer_latex": "\\frac{\\pi \\ln 2}{8}",
                "common_mistakes": [
                    "Forgetting to transform the limits when substituting $x=\\tan\\theta$.",
                    "Trying to integrate by parts, which leads in circles here.",
                ],
                "alternative_method": (
                    "Introduce $I(a) = \\int_0^1 \\frac{\\ln(1+ax)}{1+x^2}dx$ and "
                    "differentiate under the integral sign with respect to $a$."
                ),
                "concepts": [
                    "trigonometric substitution",
                    "symmetry of definite integrals",
                    "Feynman's trick",
                ],
                "practice_question": "Evaluate $\\int_0^1 \\frac{\\ln(1+x)}{1+x^2}\\,dx$ but with upper limit $\\infty$.",
                "difficulty": "jee_advanced",
                "time_minutes": 12,
                "verification": {
                    "kind": "definite_integral",
                    "expression": "log(1+x)/(1+x**2)",
                    "variable": "x",
                    "lower": "0",
                    "upper": "1",
                    # The wrong variant fails verification for a real reason,
                    # so the refuted path is exercised end to end.
                    "result": "1/2" if wrong else "pi*log(2)/8",
                    "roots": [],
                },
            }
        )

    async def check(self) -> ProviderStatus:
        return ProviderStatus(
            provider=self.name,
            reachable=True,
            models={t.value: self.model_for(t) for t in ModelTier},
            detail="mock provider — no network, no quota used",
            key_fingerprint="n/a",
        )
