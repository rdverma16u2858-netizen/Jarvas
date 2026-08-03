"""
Tests for the solve loop.
═══════════════════════════════════════════════════════════════════════════

WHAT MATTERS HERE
    The solver's job is not "call the model". It is: call the model, CHECK the
    answer, and do something sensible when the check fails.

    So the key test is `test_a_refuted_answer_triggers_a_retry_that_fixes_it`,
    which scripts a provider that answers wrongly the first time and correctly
    the second — the exact shape of the Phase 1 failure — and asserts the loop
    recovers.

NO NETWORK
    A scripted provider returns canned JSON, so these run in milliseconds and
    spend no quota.
"""

import json

import pytest

from app.llm.base import LLMProvider, LLMResponse, ModelTier, ProviderStatus
from app.math.schema import ClaimKind, Difficulty, Topic
from app.math.solver import Solver, SolverError, StreamingSolver
from app.math.verifier import VerdictKind

pytestmark = pytest.mark.asyncio


def solution_json(*, result: str, exact: str = "pi*log(2)/8") -> str:
    """A complete, schema-valid solution whose claimed value is `result`."""
    return json.dumps(
        {
            "topic": Topic.INTEGRAL_CALCULUS.value,
            "steps": [
                {
                    "number": 1,
                    "action": "Substitute x = tan(theta)",
                    "expression": "I = \\int_0^{\\pi/4} \\ln(1+\\tan\\theta)\\,d\\theta",
                    "justification": "The 1+x^2 in the denominator becomes sec^2, which cancels dx",
                }
            ],
            "formulas_used": ["\\tan(\\pi/4 - \\theta)"],
            "final_answer": f"{result}",
            "answer_latex": "\\frac{\\pi \\ln 2}{8}",
            "common_mistakes": ["Forgetting to transform the limits with the substitution"],
            "alternative_method": "Differentiate under the integral sign",
            "concepts": ["trigonometric substitution", "symmetry"],
            "practice_question": "Evaluate the integral of ln(1+x)/(1+x^2) from 0 to infinity",
            "difficulty": Difficulty.JEE_ADVANCED.value,
            "time_minutes": 12,
            "verification": {
                "kind": ClaimKind.NUMERIC.value,
                "expression": exact,
                "variable": "x",
                "lower": "",
                "upper": "",
                "result": result,
                "roots": [],
            },
        }
    )


class ScriptedProvider(LLMProvider):
    """Returns a queued list of replies, one per call. Records what it was sent."""

    name = "scripted"

    def __init__(self, replies: list[str]) -> None:
        self._replies = list(replies)
        self.calls: list[list] = []

    def model_for(self, tier: ModelTier) -> str:
        return f"scripted-{tier.value}"

    async def _complete(self, messages, *, tier, system, max_tokens, json_schema):
        self.calls.append(list(messages))
        text = self._replies.pop(0) if self._replies else "{}"
        return LLMResponse(
            text=text,
            model=self.model_for(tier),
            provider=self.name,
            latency_ms=0.0,
            input_tokens=10,
            output_tokens=20,
        )

    async def _stream(self, messages, *, tier, system, max_tokens, json_schema=None):
        """Stream the next queued reply in pieces, as a real provider would.

        Chunked rather than yielded whole so the SSE reassembly on the client
        is genuinely exercised — a single-chunk stream would pass even if the
        buffering logic were broken.
        """
        self.calls.append(list(messages))
        text = self._replies.pop(0) if self._replies else "{}"
        for i in range(0, len(text), 64):
            yield text[i : i + 64]

    async def check(self) -> ProviderStatus:
        return ProviderStatus(provider=self.name, reachable=True, models={})


# ── the happy path ────────────────────────────────────────────────────────


async def test_a_correct_answer_is_verified_on_the_first_attempt() -> None:
    provider = ScriptedProvider([solution_json(result="0.2721982613")])

    result = await Solver(provider).solve("integral", use_cache=False)

    assert result.verified is True
    assert result.verdict.kind is VerdictKind.VERIFIED
    assert len(result.attempts) == 1, "a correct answer must not trigger a retry"


# ── the case this whole phase exists for ──────────────────────────────────


async def test_a_refuted_answer_triggers_a_retry_that_fixes_it() -> None:
    """The Phase 1 failure, scripted and recovered.

    First reply carries the wrong decimal a live model actually produced;
    second carries the right one. The loop must catch the first and return
    the second.
    """
    provider = ScriptedProvider(
        [
            solution_json(result="0.2721982973"),  # wrong at the 8th figure
            solution_json(result="0.2721982613"),  # correct
        ]
    )

    result = await Solver(provider).solve("integral", use_cache=False)

    assert len(result.attempts) == 2, "the wrong answer should have been retried"
    assert result.attempts[0].verdict.kind is VerdictKind.REFUTED
    assert result.verified is True, "the corrected answer should be returned"
    assert "0.2721982613" in result.solution.final_answer


async def test_the_retry_prompt_tells_the_model_what_sympy_computed() -> None:
    """ "Try again" is far weaker than "you said X, the correct value is Y"."""
    provider = ScriptedProvider(
        [solution_json(result="0.2721982973"), solution_json(result="0.2721982613")]
    )

    await Solver(provider).solve("integral", use_cache=False)

    retry_prompt = provider.calls[1][-1].content
    assert "0.2721982973" in retry_prompt, "must quote what the model claimed"
    assert "0.272198261" in retry_prompt, "must quote the correct value"
    assert "work the problem again" in retry_prompt.lower()


async def test_two_wrong_answers_are_returned_flagged_not_hidden() -> None:
    """A student left with nothing is worse off than one given a warning."""
    provider = ScriptedProvider(
        [solution_json(result="0.2721982973"), solution_json(result="0.2721982999")]
    )

    result = await Solver(provider).solve("integral", use_cache=False)

    assert len(result.attempts) == 2
    assert result.verified is False
    assert result.verdict.kind is VerdictKind.REFUTED
    assert result.solution is not None, "the answer must still be returned"
    assert result.verdict.expected, "and it must say what the correct value is"


async def test_retry_stops_after_max_attempts() -> None:
    """Retrying forever would burn a free-tier daily quota in one request."""
    provider = ScriptedProvider([solution_json(result="0.1")] * 5)

    await Solver(provider).solve("integral", use_cache=False, max_attempts=2)

    assert len(provider.calls) == 2, "must stop at max_attempts, not keep retrying"


# ── verdicts that must NOT trigger a retry ────────────────────────────────


async def test_an_unverifiable_answer_is_not_retried() -> None:
    """A proof has nothing to check; asking again would check nothing again."""
    payload = json.loads(solution_json(result="anything"))
    payload["verification"] = {
        "kind": ClaimKind.NONE.value,
        "expression": "",
        "variable": "x",
        "lower": "",
        "upper": "",
        "result": "",
        "roots": [],
    }
    provider = ScriptedProvider([json.dumps(payload)])

    result = await Solver(provider).solve("prove that sqrt(2) is irrational", use_cache=False)

    assert len(result.attempts) == 1
    assert result.verdict.kind is VerdictKind.UNVERIFIABLE
    assert result.verified is False, "unverifiable must not be reported as verified"


# ── failure handling ──────────────────────────────────────────────────────


async def test_malformed_model_output_raises_solver_error() -> None:
    provider = ScriptedProvider(["this is not json at all"])

    with pytest.raises(SolverError, match="schema"):
        await Solver(provider).solve("anything", use_cache=False)


async def test_a_broken_retry_still_returns_the_first_attempt() -> None:
    """Losing a usable first answer because the retry failed would be a regression."""
    provider = ScriptedProvider([solution_json(result="0.2721982973"), "garbage"])

    result = await Solver(provider).solve("integral", use_cache=False)

    assert len(result.attempts) == 1
    assert result.verified is False
    assert result.solution.final_answer == "0.2721982973"


# ── streaming ─────────────────────────────────────────────────────────────


async def test_stream_emits_stages_then_a_result() -> None:
    provider = ScriptedProvider([solution_json(result="0.2721982613")])

    events = [e async for e in StreamingSolver(provider).solve_stream("integral")]
    kinds = [e.type for e in events]

    assert kinds[0] == "stage", "the UI needs a stage before anything else"
    assert "delta" in kinds, "progress must be observable while generating"
    assert kinds[-1] == "result"
    assert events[-1].payload["verified"] is True


async def test_stream_retries_a_refuted_answer_like_the_plain_endpoint() -> None:
    """The two paths must agree.

    A student on the streaming UI getting an uncorrected wrong answer that the
    non-streaming endpoint would have fixed is the kind of inconsistency that
    quietly undermines the whole verification promise.
    """
    provider = ScriptedProvider(
        [solution_json(result="0.2721982973"), solution_json(result="0.2721982613")]
    )

    events = [e async for e in StreamingSolver(provider).solve_stream("integral")]

    assert len(provider.calls) == 2, "the refuted answer should have been retried"
    assert events[-1].payload["verified"] is True

    # The retry must be announced — a silent second wait reads as a stall.
    stages = [e.message for e in events if e.type == "stage"]
    assert any("again" in m for m in stages), stages


async def test_stream_reports_a_provider_failure_as_an_event() -> None:
    """After the response has begun, an HTTP status can no longer be changed,
    so the failure has to travel in-band or the client sees 200 and silence."""
    from app.llm.errors import ProviderError

    class Broken(ScriptedProvider):
        async def _stream(self, messages, *, tier, system, max_tokens, json_schema=None):
            raise ProviderError("upstream exploded")
            yield ""  # pragma: no cover — makes this an async generator

    events = [e async for e in StreamingSolver(Broken([])).solve_stream("x")]

    assert events[-1].type == "error"
    assert "exploded" in events[-1].message
