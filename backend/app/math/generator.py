"""
The question generator — write practice questions, then check the answer key.
═══════════════════════════════════════════════════════════════════════════

THE PIPELINE

    ask the model for N questions
            |
    parse into QuestionSet
            |
    for each question:
        structural check  (options contain the answer? indices in range?)
            |
        SymPy verification of the claimed answer
            |
    partition into confirmed / unconfirmed

WHY UNCONFIRMED QUESTIONS ARE KEPT, NOT DELETED
    Two thirds of a set can legitimately fail to verify: proofs have nothing
    computable in them, and `kind: none` is the honest answer for those. If
    unverified questions were dropped, asking for five proof questions would
    return zero, which looks exactly like a broken feature.

    So they are returned, labelled. The API reports how many were confirmed,
    and the caller decides. A quiz (Phase 7) can ask for confirmed-only; a
    student browsing practice can see everything with the unverified ones
    marked.

WHY REFUTED QUESTIONS ARE DROPPED
    This is the one case where deletion is right. REFUTED means SymPy
    recomputed the answer and got something else — the answer key is WRONG.
    Serving it would have the student mark their own correct work as a
    mistake, which is the specific harm this whole layer exists to prevent.

WHY GENERATION IS NOT RETRIED THE WAY SOLVING IS
    A refuted solution is one problem the student is waiting on, so the solver
    spends a second call to fix it. Here a set of ten might have one bad
    answer key; regenerating the whole set to fix it would throw away nine good
    questions and spend another full request. Dropping the one is cheaper and
    loses less.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

from pydantic import ValidationError

from app.core.config import settings
from app.core.logging import get_logger
from app.llm.base import LLMProvider, Message, ModelTier
from app.llm.errors import LLMError
from app.math.prompts import GENERATOR_SYSTEM, generation_request
from app.math.questions import QUESTION_SET_SCHEMA, Question, QuestionSet, QuestionType
from app.math.schema import Difficulty, Topic
from app.math.verifier import Verdict, VerdictKind, verify

logger = get_logger(__name__)

#: Upper bound on questions per request. Beyond this the reply routinely hits
#: the token ceiling and truncates mid-object, which fails schema validation
#: and costs the whole call — a slow, expensive way to get nothing.
MAX_QUESTIONS = 20


class GenerationError(Exception):
    """No questions could be produced at all (not: some failed to verify)."""


@dataclass
class GeneratedQuestion:
    """One question with the outcome of checking its answer key."""

    question: Question
    verdict: Verdict

    @property
    def confirmed(self) -> bool:
        """True only when SymPy independently recomputed the answer."""
        return self.verdict.ok


@dataclass
class GenerationResult:
    questions: list[GeneratedQuestion] = field(default_factory=list)
    #: Questions whose answer key SymPy contradicted. Never served to a
    #: student; surfaced so the failure is visible rather than silent.
    rejected: list[tuple[Question, Verdict]] = field(default_factory=list)
    model: str = ""
    total_ms: float = 0.0

    @property
    def confirmed_count(self) -> int:
        return sum(1 for q in self.questions if q.confirmed)


class Generator:
    """Generates practice questions and verifies every answer key."""

    def __init__(self, provider: LLMProvider) -> None:
        self._llm = provider

    async def generate(
        self,
        *,
        topic: Topic,
        difficulty: Difficulty,
        question_type: QuestionType,
        count: int = 5,
        concepts: str = "",
        avoid: list[str] | None = None,
        tier: ModelTier = ModelTier.FAST,
        use_cache: bool = True,
    ) -> GenerationResult:
        """Generate `count` questions, then check each answer with SymPy.

        Raises GenerationError only when nothing usable came back — the
        provider failed, or the reply did not fit the schema. Individual
        questions failing verification is a normal outcome, not an error.
        """
        started = time.perf_counter()
        count = max(1, min(count, MAX_QUESTIONS))

        prompt = generation_request(
            topic=topic.value,
            difficulty=difficulty.value,
            question_type=question_type.value,
            count=count,
            concepts=concepts,
            avoid=avoid,
        )

        try:
            response = await self._llm.complete(
                [Message(role="user", content=prompt)],
                tier=tier,
                system=GENERATOR_SYSTEM,
                json_schema=QUESTION_SET_SCHEMA,
                # Caching is off by default at the route level for this
                # endpoint: a student pressing "more questions" wants MORE
                # questions, and a cache hit would return the same set.
                use_cache=use_cache,
                cache_ttl=settings.LLM_CACHE_TTL,
            )
        except LLMError as exc:
            raise GenerationError(f"the model could not be reached: {exc}") from exc

        try:
            parsed = QuestionSet.model_validate_json(response.text)
        except ValidationError as exc:
            raise GenerationError(
                f"the model's reply did not match the question schema: {exc}"
            ) from exc

        if not parsed.questions:
            raise GenerationError("the model returned an empty question set")

        result = GenerationResult(model=response.model)

        # Verification is independent per question, so run them together. A
        # set of ten otherwise waits out ten sequential SymPy calls, and a
        # slow integral in position one delays every question behind it.
        verdicts = await asyncio.gather(
            *(self._check(question) for question in parsed.questions)
        )

        for index, (question, verdict) in enumerate(
            zip(parsed.questions, verdicts, strict=True), start=1
        ):
            # Renumber from the set's own order. The model's numbering drifts
            # when it drops or reorders a question, and the number is what the
            # student sees.
            question.number = index

            if verdict.kind is VerdictKind.REFUTED:
                logger.warning(
                    "generated question %d had a wrong answer key: claimed=%s expected=%s",
                    index,
                    verdict.claimed,
                    verdict.expected,
                )
                result.rejected.append((question, verdict))
                continue

            result.questions.append(GeneratedQuestion(question=question, verdict=verdict))

        if not result.questions:
            raise GenerationError(
                "every generated question had an answer key SymPy contradicted"
            )

        # Renumber again over the surviving questions, so a student never sees
        # "1, 2, 4" and wonders what happened to 3.
        for position, generated in enumerate(result.questions, start=1):
            generated.question.number = position

        result.total_ms = round((time.perf_counter() - started) * 1000, 1)
        logger.info(
            "generated %d questions (%d confirmed, %d rejected) in %.0fms",
            len(result.questions),
            result.confirmed_count,
            len(result.rejected),
            result.total_ms,
        )
        return result

    @staticmethod
    async def _check(question: Question) -> Verdict:
        """Structural check first, then SymPy.

        Order matters: a question whose options do not contain its answer is
        unusable no matter what the mathematics says, and reporting it as
        "verified" because the integral happened to be right would be the
        wrong answer to the wrong question.
        """
        problem = question.structural_problem()
        if problem:
            return Verdict(
                kind=VerdictKind.ERROR,
                detail=f"malformed question: {problem}",
            )
        return await verify(question.verification)
