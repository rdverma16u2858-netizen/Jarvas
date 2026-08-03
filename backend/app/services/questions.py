"""
Question bank storage and selection.
═══════════════════════════════════════════════════════════════════════════

WHAT THIS LAYER IS FOR
    The generator makes questions; this decides which ones a student sees and
    records how they did. Keeping the two apart means Phase 7's quizzes can
    draw from the bank without going near an LLM call, and Phase 8 can read
    the same counters for progress without re-deriving them.

ON `recent_prompts`
    Fed back to the generator as `avoid`. This is the difference between a
    practice feature and a party trick: without it, the second request for
    "five medium integrals" returns substantially the first five, because the
    model has no memory across calls and every one of them converges on the
    same handful of textbook examples.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.math.generator import GeneratedQuestion
from app.models.question import PracticeQuestion

logger = get_logger(__name__)

#: How many previously generated prompts are shown to the model as "not these
#: again". Twenty is enough to break the textbook-favourites rut; far more
#: would crowd the instructions out of the request.
AVOID_WINDOW = 20


class QuestionService:
    """All reads and writes for the practice question bank."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # ── writing ───────────────────────────────────────────────────────────

    async def save(self, generated: GeneratedQuestion, *, model: str = "") -> PracticeQuestion:
        """Store one generated question with the verdict on its answer key."""
        question = generated.question
        row = PracticeQuestion(
            topic=question.topic.value,
            difficulty=question.difficulty.value,
            type=question.type.value,
            prompt=question.prompt,
            answer=question.answer,
            verified=generated.confirmed,
            verdict_kind=generated.verdict.kind.value,
            question=question.model_dump(mode="json"),
            verdict={
                "kind": generated.verdict.kind.value,
                "detail": generated.verdict.detail,
                "expected": generated.verdict.expected,
                "claimed": generated.verdict.claimed,
                "checks": generated.verdict.checks,
            },
            model=model,
        )
        self.db.add(row)
        await self.db.flush()
        return row

    async def save_all(
        self, generated: list[GeneratedQuestion], *, model: str = ""
    ) -> list[PracticeQuestion]:
        return [await self.save(item, model=model) for item in generated]

    # ── reading ───────────────────────────────────────────────────────────

    async def get(self, question_id: int) -> PracticeQuestion | None:
        return await self.db.get(PracticeQuestion, question_id)

    async def list(
        self,
        *,
        topic: str | None = None,
        difficulty: str | None = None,
        question_type: str | None = None,
        verified_only: bool = False,
        unattempted_only: bool = False,
        bookmarked_only: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> list[PracticeQuestion]:
        """Browse the bank, newest first. Every filter is optional and they
        compose."""
        statement = select(PracticeQuestion)

        if topic:
            statement = statement.where(PracticeQuestion.topic == topic)
        if difficulty:
            statement = statement.where(PracticeQuestion.difficulty == difficulty)
        if question_type:
            statement = statement.where(PracticeQuestion.type == question_type)
        if verified_only:
            statement = statement.where(PracticeQuestion.verified.is_(True))
        if unattempted_only:
            statement = statement.where(PracticeQuestion.attempts == 0)
        if bookmarked_only:
            statement = statement.where(PracticeQuestion.bookmarked.is_(True))

        return list(
            (
                await self.db.execute(
                    statement.order_by(PracticeQuestion.id.desc()).limit(limit).offset(offset)
                )
            )
            .scalars()
            .all()
        )

    async def recent_prompts(
        self, *, topic: str, difficulty: str | None = None, limit: int = AVOID_WINDOW
    ) -> list[str]:
        """The last few prompts on a topic, to pass to the generator as `avoid`.

        Deliberately NOT filtered by question type: the same integral asked as
        multiple choice and then as short answer is still the same integral,
        and a student notices.
        """
        statement = select(PracticeQuestion.prompt).where(PracticeQuestion.topic == topic)
        if difficulty:
            statement = statement.where(PracticeQuestion.difficulty == difficulty)

        return list(
            (
                await self.db.execute(
                    statement.order_by(PracticeQuestion.id.desc()).limit(limit)
                )
            )
            .scalars()
            .all()
        )

    # ── the student's progress ────────────────────────────────────────────

    async def record_attempt(
        self, question_id: int, *, correct: bool
    ) -> PracticeQuestion | None:
        """Count one attempt. Idempotency is deliberately NOT enforced.

        Re-attempting a question you got wrong is the point of practice, so
        every attempt counts — including repeats of the same question. Phase 8
        reads `attempts` and `correct` together, and a question answered right
        on the third try is genuinely different from one answered right first
        time.
        """
        question = await self.get(question_id)
        if question is None:
            return None

        question.attempts += 1
        if correct:
            question.correct += 1
        question.last_attempt_at = datetime.now(UTC)
        await self.db.flush()
        return question

    async def set_bookmark(
        self, question_id: int, bookmarked: bool
    ) -> PracticeQuestion | None:
        question = await self.get(question_id)
        if question is None:
            return None
        question.bookmarked = bookmarked
        await self.db.flush()
        return question

    async def delete(self, question_id: int) -> bool:
        question = await self.get(question_id)
        if question is None:
            return False
        await self.db.delete(question)
        await self.db.flush()
        return True

    # ── aggregate ─────────────────────────────────────────────────────────

    async def stats_by_topic(self) -> list[dict]:
        """Per-topic counts — the raw material for Phase 8's progress view.

        Kept here rather than in the progress phase because it is a question
        about the bank, and putting it anywhere else would mean a second
        module writing queries against these tables.
        """
        rows = await self.db.execute(
            select(
                PracticeQuestion.topic,
                func.count(PracticeQuestion.id),
                func.sum(PracticeQuestion.attempts),
                func.sum(PracticeQuestion.correct),
            ).group_by(PracticeQuestion.topic)
        )

        return [
            {
                "topic": topic,
                "questions": total,
                "attempts": int(attempts or 0),
                "correct": int(correct or 0),
                # None, not 0, when nothing has been attempted: "0% correct"
                # and "not started" look identical otherwise, and they mean
                # very different things.
                "accuracy": (
                    round(int(correct or 0) / int(attempts), 3) if attempts else None
                ),
            }
            for topic, total, attempts, correct in rows.all()
        ]
