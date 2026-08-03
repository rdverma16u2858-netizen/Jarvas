"""
The reviewer — read a student's working, and defend it against the model.
═══════════════════════════════════════════════════════════════════════════

THE PIPELINE

    ask the model to review the work
            |
    parse into Review
            |
    SymPy checks TWO things, independently:
            |
        verification   is the reviewer's "correct answer" actually correct?
        student_check  is the student's answer equivalent to it?
            |
    RECONCILE  ->  the verdict the student is shown

RECONCILIATION IS THE WHOLE POINT
    A language model asked "is this right?" is agreeable. Ask it to find
    mistakes and it will find mistakes, because that is what it was asked to
    do. Left alone it will occasionally mark correct work wrong, with complete
    confidence and a plausible explanation.

    That is the one failure this phase cannot ship. So the model's verdict is
    not final: it is a claim, checked against SymPy like every other claim in
    this codebase.

    · SymPy confirms the correct answer AND confirms the student matched it
      -> the student was right. The verdict is overridden if it said otherwise.

    · SymPy confirms the correct answer AND refutes the student's
      -> the student was wrong. A "correct" verdict is overridden.

    · SymPy could not confirm the correct answer
      -> nothing is overridden, and the review is marked unconfirmed. Acting
         on an unverified reference answer would just be trusting the model
         twice.

WHY OVERRIDING TO "RIGHT ANSWER, FLAWED WORKING" RATHER THAN "CORRECT"
    A confirmed answer means the ANSWER is right. It says nothing about the
    working. If the reviewer found a genuine fatal error and the answer still
    came out right, both facts are true at once — two sign errors cancel, and
    the student needs to know. Only an answer that is right with no fatal
    mistakes found becomes plain `correct`.
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
from app.math.prompts import REVIEWER_SYSTEM, review_request
from app.math.review import REVIEW_SCHEMA, Review, ReviewVerdict, Severity
from app.math.schema import ClaimKind
from app.math.verifier import Verdict, VerdictKind, verify

logger = get_logger(__name__)

#: Longest student submission accepted. Beyond this the review loses the
#: thread — and a "solution" this long is usually a whole problem set pasted
#: at once, which needs splitting rather than reviewing.
MAX_WORKING_CHARS = 8000


class ReviewError(Exception):
    """The work could not be reviewed at all (not: was found to be wrong)."""


@dataclass
class ReviewResult:
    """A review, after SymPy has had its say."""

    review: Review
    #: Did SymPy confirm the reviewer's own correct answer?
    answer_verdict: Verdict = field(default_factory=lambda: Verdict(kind=VerdictKind.ERROR))
    #: Did SymPy confirm the student's answer equals it?
    student_verdict: Verdict = field(default_factory=lambda: Verdict(kind=VerdictKind.ERROR))
    #: Set when reconciliation changed the model's verdict.
    overridden_from: ReviewVerdict | None = None
    model: str = ""
    total_ms: float = 0.0

    @property
    def verified(self) -> bool:
        """True when SymPy independently confirmed the reference answer.

        The student sees this as "checked" versus "not checked". A review
        whose own reference answer is unconfirmed is one model's opinion, and
        is presented that way.
        """
        return self.answer_verdict.ok

    @property
    def student_was_right(self) -> bool | None:
        """True / False / None when it could not be determined."""
        if not self.answer_verdict.ok:
            return None
        if self.student_verdict.kind is VerdictKind.VERIFIED:
            return True
        if self.student_verdict.kind is VerdictKind.REFUTED:
            return False
        return None

    @property
    def fatal_mistakes(self) -> list:
        return [m for m in self.review.mistakes if m.severity is Severity.FATAL]


class Reviewer:
    """Reviews a student's working and checks the review itself."""

    def __init__(self, provider: LLMProvider) -> None:
        self._llm = provider

    async def review(
        self,
        problem: str,
        working: str,
        *,
        tier: ModelTier = ModelTier.BALANCED,
        use_cache: bool = True,
    ) -> ReviewResult:
        """Review `working` as an attempt at `problem`.

        Raises ReviewError only when no review could be produced. A review
        finding the work wrong is a normal result, not an error.
        """
        started = time.perf_counter()

        if not working.strip():
            raise ReviewError("there is no working to review")

        try:
            response = await self._llm.complete(
                [Message(role="user", content=review_request(problem, working))],
                tier=tier,
                system=REVIEWER_SYSTEM,
                json_schema=REVIEW_SCHEMA,
                use_cache=use_cache,
                cache_ttl=settings.LLM_CACHE_TTL,
            )
        except LLMError as exc:
            raise ReviewError(f"the model could not be reached: {exc}") from exc

        try:
            review = Review.model_validate_json(response.text)
        except ValidationError as exc:
            raise ReviewError(
                f"the model's reply did not match the review schema: {exc}"
            ) from exc

        # Both checks are independent, so run them together rather than
        # waiting out two sequential SymPy calls.
        answer_verdict, student_verdict = await asyncio.gather(
            verify(review.verification),
            verify(review.student_check),
        )

        result = ReviewResult(
            review=review,
            answer_verdict=answer_verdict,
            student_verdict=student_verdict,
            model=response.model,
        )

        _reconcile(result)

        result.total_ms = round((time.perf_counter() - started) * 1000, 1)
        logger.info(
            "review %s (answer %s, student %s)%s in %.0fms",
            result.review.verdict.value,
            answer_verdict.kind.value,
            student_verdict.kind.value,
            f" [overridden from {result.overridden_from.value}]"
            if result.overridden_from
            else "",
            result.total_ms,
        )
        return result


def _reconcile(result: ReviewResult) -> None:
    """Correct the model's verdict against what SymPy actually found.

    Mutates `result` in place, recording `overridden_from` when the verdict
    changes so the override is visible rather than silent — both to the
    student and in the logs.
    """
    review = result.review

    # Without a confirmed reference answer there is nothing to reconcile
    # against. Overriding here would mean trusting the same model twice.
    if not result.answer_verdict.ok:
        return

    # The student reached no answer, or it was not comparable. The reviewer's
    # reading of the working stands.
    if review.student_check.kind is ClaimKind.NONE:
        return

    was_right = result.student_was_right
    if was_right is None:
        return

    original = review.verdict

    if was_right:
        # SymPy says the student's answer is equivalent to a confirmed correct
        # answer. They cannot be marked wrong.
        if original in (ReviewVerdict.WRONG, ReviewVerdict.UNCLEAR):
            review.verdict = (
                ReviewVerdict.RIGHT_ANSWER_FLAWED_WORKING
                if result.fatal_mistakes
                else ReviewVerdict.CORRECT
            )
            result.overridden_from = original
            logger.warning(
                "reviewer marked correct work as %s - overridden to %s",
                original.value,
                review.verdict.value,
            )
        elif original is ReviewVerdict.CORRECT and result.fatal_mistakes:
            # Right answer, but fatal errors were found in getting there.
            review.verdict = ReviewVerdict.RIGHT_ANSWER_FLAWED_WORKING
            result.overridden_from = original
    else:
        # SymPy says the student's answer differs from a confirmed correct
        # one. It cannot be called correct.
        if original in (ReviewVerdict.CORRECT, ReviewVerdict.RIGHT_ANSWER_FLAWED_WORKING):
            review.verdict = ReviewVerdict.WRONG
            result.overridden_from = original
            logger.warning(
                "reviewer marked incorrect work as %s - overridden to wrong", original.value
            )
