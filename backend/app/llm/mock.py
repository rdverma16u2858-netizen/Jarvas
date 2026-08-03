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
from app.math.schema import Difficulty, Topic


def _match_enum(text: str, enum, default) -> str:
    """Find which member of `enum` a request text names.

    Longest value first, so "integral calculus" is not swallowed by
    "calculus", and "jee advanced" not by "jee main".
    """
    for member in sorted(enum, key=lambda m: -len(m.value)):
        if member.value.replace("_", " ") in text:
            return member.value
    return default.value


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

        # When a schema is requested, return a COMPLETE, schema-valid document.
        # Returning a stub here would mean the mock works and the real provider
        # breaks — exactly the bug a mock must not introduce.
        if json_schema is not None:
            text = self._structured(messages, json_schema)

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
            self._structured(messages, json_schema)
            if json_schema is not None
            else self._answer(messages)
        )
        for word in text.split(" "):
            if self._latency:
                await asyncio.sleep(self._latency / 40)
            yield word + " "

    @classmethod
    def _structured(
        cls, messages: Sequence[Message], json_schema: dict[str, Any] | None
    ) -> str:
        """Pick the document to return from the SHAPE OF THE SCHEMA asked for.

        Dispatching on the schema rather than on keywords in the prompt means
        the mock cannot answer a generation request with a solution: if the
        caller asked for a QuestionSet, the top-level `questions` property is
        there regardless of how the request was worded.
        """
        properties = (json_schema or {}).get("properties", {})
        if "questions" in properties:
            return cls._questions_json(messages)
        if "student_check" in properties:
            return cls._review_json(messages)
        if "legibility" in properties:
            return cls._extraction_json(messages)
        return cls._solution_json(messages)

    @staticmethod
    def _extraction_json(messages: Sequence[Message]) -> str:
        """A schema-valid image extraction.

        The variants are chosen by the hint text, because the two states the
        UI has to handle well cannot be produced on demand from a real model:
        a partial reading with specific doubts, and an image nothing could be
        read from. Both are exactly the states most likely to ship broken.
        """
        last = next((m.content for m in reversed(messages) if m.role == "user"), "").lower()
        has_image = any(m.images for m in messages)

        if "unreadable" in last:
            return json.dumps(
                {
                    "problem": "",
                    "plain": "",
                    "legibility": "unreadable",
                    "uncertain": ["almost nothing in the image was legible"],
                    "topic": "other",
                    "contains_working": False,
                    "working": "",
                    "notes": "[mock] the photograph is too blurred to read.",
                }
            )

        if "unclear" in last or "partial" in last:
            return json.dumps(
                {
                    "problem": "\\int_0^1 x e^{x} \\, dx",
                    "plain": "integrate x*e^x dx from 0 to 1",
                    "legibility": "partial",
                    "uncertain": [
                        "the upper limit could be 1 or 7",
                        "unclear whether the differential is dx or dt",
                    ],
                    "topic": "integral_calculus",
                    "contains_working": False,
                    "working": "",
                    "notes": "",
                }
            )

        return json.dumps(
            {
                "problem": "\\int_0^1 x e^{x} \\, dx",
                "plain": "integrate x*e^x dx from 0 to 1",
                "legibility": "clear",
                "uncertain": [],
                "topic": "integral_calculus",
                "contains_working": "working" in last,
                "working": (
                    "Let u = x, dv = e^x dx\nv = e^x\n= x e^x - e^x + C"
                    if "working" in last
                    else ""
                ),
                # Recorded so a test can prove the image actually reached the
                # provider rather than being dropped on the way.
                "notes": "" if has_image else "[mock] no image was attached",
            }
        )

    @staticmethod
    def _review_json(messages: Sequence[Message]) -> str:
        """A schema-valid review, with the reconciliation paths reachable.

        The reviewer's verdict is deliberately made WRONG in two directions,
        selected by keywords in the submitted working, because both are
        corrections the reconciliation layer has to make and neither can be
        produced on demand from a real model:

            "actually right"  the reviewer calls sound work wrong, and SymPy
                              proves the student's answer correct. This is the
                              failure the phase exists to prevent.
            "falsely praised" the reviewer calls wrong work correct, and SymPy
                              proves the answer differs.

        Everything else gets a straightforward review of a genuine sign error
        in integration by parts, whose reference answer verifies.
        """
        last = next((m.content for m in reversed(messages) if m.role == "user"), "").lower()

        # The reference answer throughout: d/dx of it is x*exp(x).
        correct = "x*exp(x) - exp(x)"

        if "actually right" in last:
            # Sound work, wrong verdict. SymPy must overrule this.
            student_answer = "x*exp(x) - exp(x) + C"
            verdict = "wrong"
            mistakes = [
                {
                    "line": 2,
                    "quote": "v = e^x",
                    "type": "conceptual",
                    "severity": "fatal",
                    "what_went_wrong": "[mock] a fabricated objection to correct work",
                    "why_it_is_wrong": "[mock] this complaint is not valid",
                    "correction": "v = e^x",
                }
            ]
            student_side = correct
        elif "falsely praised" in last:
            # Wrong work, wrong verdict. SymPy must overrule this too.
            student_answer = "x*exp(x) + exp(x) + C"
            verdict = "correct"
            mistakes = []
            student_side = "x*exp(x) + exp(x)"
        else:
            student_answer = "x*exp(x) + exp(x) + C"
            verdict = "wrong"
            mistakes = [
                {
                    "line": 3,
                    "quote": "= x e^x + e^x + C",
                    "type": "sign",
                    "severity": "fatal",
                    "what_went_wrong": (
                        "The integral of $v\\,du$ was added instead of subtracted."
                    ),
                    "why_it_is_wrong": (
                        "Integration by parts is $\\int u\\,dv = uv - \\int v\\,du$. "
                        "The second term is subtracted."
                    ),
                    "correction": "= x e^x - e^x + C",
                }
            ]
            student_side = "x*exp(x) + exp(x)"

        return json.dumps(
            {
                "student_answer": student_answer,
                "mistakes": mistakes,
                "verdict": verdict,
                "summary": "[mock] review of an integration by parts attempt.",
                "what_went_well": "Choosing $u = x$ and $dv = e^x dx$ was the right split.",
                "corrected_working": [
                    "\\int x e^x dx = x e^x - \\int e^x dx",
                    "= x e^x - e^x + C",
                ],
                "correct_answer": "x e^x - e^x + C",
                "correct_answer_latex": "x e^x - e^x + C",
                "topic": "integral_calculus",
                "difficulty": "jee_main",
                "concept_to_review": "integration by parts",
                "verification": {
                    "kind": "indefinite_integral",
                    "expression": "x*exp(x)",
                    "variable": "x",
                    "lower": "",
                    "upper": "",
                    "result": correct,
                    "roots": [],
                },
                "student_check": {
                    "kind": "expression_equality",
                    "expression": student_side,
                    "variable": "x",
                    "lower": "",
                    "upper": "",
                    "result": correct,
                    "roots": [],
                },
            }
        )

    @staticmethod
    def _requested(messages: Sequence[Message]) -> tuple[str, int, str, str]:
        """Recover (question type, count, topic, difficulty) from the request.

        The mock must produce a set that matches what was ASKED for, not a
        fixed fixture. Two reasons, both learned the hard way:

        · Structure — options on a multiple_choice question and none on a
          proof, or the generator's structural check rejects its own fixture.
        · Topic and difficulty — anything reading the question bank back
          (quiz selection, progress, adaptive difficulty) filters on these.
          A mock that always answered "calculus / medium" made every such
          filter appear broken while the real code was correct.
        """
        last = next((m.content for m in reversed(messages) if m.role == "user"), "")
        lowered = last.lower()

        # Longest first: "multiple correct" contains "multiple", and
        # "true false" must not be mistaken for anything else.
        for phrase, value in (
            ("multiple correct", "multiple_correct"),
            ("multiple choice", "multiple_choice"),
            ("short answer", "short_answer"),
            ("true false", "true_false"),
            ("numerical", "numerical"),
            ("proof", "proof"),
        ):
            if phrase in lowered:
                question_type = value
                break
        else:
            question_type = "short_answer"

        count = 3
        for word in lowered.split():
            if word.isdigit():
                count = max(1, min(int(word), 20))
                break

        # `generation_request` writes "... on <topic>, at <difficulty>
        # difficulty", with underscores rendered as spaces. Matched against
        # the real enums rather than parsed loosely, so an unrecognised value
        # falls back rather than being invented.
        topic = _match_enum(lowered, Topic, default=Topic.CALCULUS)
        difficulty = _match_enum(lowered, Difficulty, default=Difficulty.MEDIUM)

        return question_type, count, topic, difficulty

    @classmethod
    def _questions_json(cls, messages: Sequence[Message]) -> str:
        """A schema-valid question set, matching the type that was requested.

        The set deliberately contains a mix: real, verifiable derivative
        questions, plus — when the request mentions "wrong" — one whose answer
        key SymPy will contradict. Without that, the generator's rejection path
        would only ever run against a live model making a real mistake, which
        is not reproducible on demand and therefore the path most likely to
        ship broken.
        """
        question_type, count, topic, difficulty = cls._requested(messages)
        last = next((m.content for m in reversed(messages) if m.role == "user"), "").lower()
        include_wrong = "wrong" in last

        is_choice = question_type in {"multiple_choice", "multiple_correct"}
        is_proof = question_type == "proof"

        questions = []
        for index in range(count):
            # Each question differentiates x**n for a different n, so the set
            # is not four copies of one fixture.
            power = index + 2
            # SymPy syntax for the verification claim; x**1 verifies fine but
            # reads badly, and the mock is what the UI is developed against.
            derivative = f"{power}*x" if power == 2 else f"{power}*x**{power - 1}"
            broken = include_wrong and index == 0

            # Options go through the LaTeX renderer, so they must be LaTeX —
            # the real model is instructed to write them that way, and a mock
            # that emits SymPy syntax would have the UI built against
            # "2∗x∗∗5" instead of rendered mathematics.
            def tex(coefficient: int, exponent: int) -> str:
                base = "x" if exponent == 1 else f"x^{{{exponent}}}"
                return base if coefficient == 1 else f"{coefficient}{base}"

            question: dict[str, Any] = {
                "number": index + 1,
                "type": question_type,
                "topic": topic,
                "difficulty": difficulty,
                "prompt": f"Differentiate $f(x) = x^{{{power}}}$ with respect to $x$.",
                "options": (
                    [
                        # Each distractor is one specific mistake: the right
                        # answer, the exponent kept, the exponent not reduced,
                        # and the antiderivative taken instead.
                        tex(power, power - 1),
                        tex(1, power - 1),
                        tex(power, power),
                        f"\\frac{{{tex(1, power + 1)}}}{{{power + 1}}}",
                    ]
                    if is_choice
                    else []
                ),
                "solution_outline": [
                    "Apply the power rule.",
                    f"$\\frac{{d}}{{dx}}x^{{{power}}} = {tex(power, power - 1)}$.",
                ],
                "hint": "The power rule drops the exponent to the front.",
                # For a choice question the answer must be the TEXT of the
                # correct option, which is option 0.
                "answer": tex(power, power - 1) if is_choice else derivative,
                "answer_latex": tex(power, power - 1),
                "correct_options": [0] if is_choice else [],
                "concepts": ["power rule", "differentiation"],
                "time_minutes": 2,
                "verification": {
                    "kind": "none" if is_proof else "derivative",
                    "expression": "" if is_proof else f"x**{power}",
                    "variable": "x",
                    "lower": "",
                    "upper": "",
                    # The broken variant fails verification for a real reason,
                    # so the rejection path is exercised end to end.
                    "result": "" if is_proof else ("x**2" if broken else derivative),
                    "roots": [],
                },
            }

            if is_proof:
                question["prompt"] = (
                    f"Prove that $\\frac{{d}}{{dx}}x^{{{power}}} = {tex(power, power - 1)}$ "
                    "from the definition of the derivative."
                )
                question["answer"] = "See the outline: expand the binomial and take the limit."

            questions.append(question)

        return json.dumps({"questions": questions})

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
