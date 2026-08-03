"""
Quiz assembly, timing and grading.
═══════════════════════════════════════════════════════════════════════════

WHY A QUIZ ONLY EVER DRAWS VERIFIED QUESTIONS
    Practice browsing shows unconfirmed questions with a warning, because a
    proof question legitimately has nothing computable in it and hiding those
    would empty the feature.

    A quiz is different: it SCORES you. Marking a student wrong against an
    answer key that nothing checked is precisely the harm Phase 5 was built to
    prevent, and here it would be attached to a number they take seriously.
    So the selection is verified-only, with no option to relax it.

WHY UNATTEMPTED QUESTIONS ARE PREFERRED, NOT REQUIRED
    A quiz made only of questions you have never seen is the ideal. A quiz
    that refuses to start because you have seen them all is useless. Fresh
    questions are drawn first and seen ones top up the rest.

GRADING HAPPENS ONCE, AT SUBMIT
    Answers are stored ungraded while the paper is open. `is_correct` is NULL
    for "not yet marked", which is a different thing from False, and the
    column is nullable so the two cannot be confused.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.question import PracticeQuestion
from app.models.quiz import (
    DEFAULT_SECONDS_PER_QUESTION,
    MARKING,
    Quiz,
    QuizAnswer,
    QuizMode,
    QuizStatus,
)

logger = get_logger(__name__)

#: Most questions one paper may contain. A JEE Advanced paper is 54; beyond
#: about this the selection query and the page both stop being pleasant.
MAX_QUESTIONS = 60


class QuizError(Exception):
    """A quiz could not be assembled or an action was not allowed."""


class NotEnoughQuestionsError(QuizError):
    """The bank does not hold enough verified questions to build the paper."""

    def __init__(self, wanted: int, found: int) -> None:
        self.wanted = wanted
        self.found = found
        super().__init__(
            f"only {found} verified questions available, {wanted} requested — "
            "generate more practice questions on this topic first"
        )


class QuizService:
    """Assembly, timing and grading for quizzes and mock tests."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # ── selection ─────────────────────────────────────────────────────────

    async def _draw(
        self,
        *,
        count: int,
        topic: str | None,
        difficulty: str | None,
        question_type: str | None,
    ) -> list[PracticeQuestion]:
        """Pick `count` verified questions, unseen ones first.

        Two queries rather than one ORDER BY: expressing "unattempted first,
        then random within each group" portably across SQLite and Postgres is
        more trouble than asking twice, and the second query usually returns
        nothing because the first filled the paper.
        """

        def base():
            statement = select(PracticeQuestion).where(PracticeQuestion.verified.is_(True))
            if topic:
                statement = statement.where(PracticeQuestion.topic == topic)
            if difficulty:
                statement = statement.where(PracticeQuestion.difficulty == difficulty)
            if question_type:
                statement = statement.where(PracticeQuestion.type == question_type)
            return statement

        fresh = list(
            (
                await self.db.execute(
                    base()
                    .where(PracticeQuestion.attempts == 0)
                    .order_by(func.random())
                    .limit(count)
                )
            )
            .scalars()
            .all()
        )

        if len(fresh) >= count:
            return fresh[:count]

        seen = list(
            (
                await self.db.execute(
                    base()
                    .where(PracticeQuestion.attempts > 0)
                    .order_by(func.random())
                    .limit(count - len(fresh))
                )
            )
            .scalars()
            .all()
        )
        return fresh + seen

    async def available(
        self,
        *,
        topic: str | None = None,
        difficulty: str | None = None,
        question_type: str | None = None,
    ) -> int:
        """How many verified questions a paper could be built from.

        Surfaced so the UI can say "12 available" beside the count field
        rather than letting someone request 20 and be refused.
        """
        statement = select(func.count(PracticeQuestion.id)).where(
            PracticeQuestion.verified.is_(True)
        )
        if topic:
            statement = statement.where(PracticeQuestion.topic == topic)
        if difficulty:
            statement = statement.where(PracticeQuestion.difficulty == difficulty)
        if question_type:
            statement = statement.where(PracticeQuestion.type == question_type)
        return (await self.db.execute(statement)).scalar() or 0

    # ── creating ──────────────────────────────────────────────────────────

    async def create(
        self,
        *,
        count: int = 10,
        mode: QuizMode = QuizMode.PRACTICE,
        topic: str | None = None,
        difficulty: str | None = None,
        question_type: str | None = None,
        time_limit_seconds: int | None = None,
        title: str = "",
    ) -> Quiz:
        """Assemble a paper and start its clock.

        Raises NotEnoughQuestionsError when the bank cannot fill it — deliberately
        rather than quietly returning a shorter paper, because a mock test
        that silently shrinks is not the thing that was asked for.
        """
        count = max(1, min(count, MAX_QUESTIONS))

        questions = await self._draw(
            count=count, topic=topic, difficulty=difficulty, question_type=question_type
        )
        if len(questions) < count:
            raise NotEnoughQuestionsError(wanted=count, found=len(questions))

        # Shuffled once here rather than ordered by id, so a paper drawn twice
        # from the same pool does not present in the same sequence.
        secrets.SystemRandom().shuffle(questions)

        marks_correct, marks_wrong = MARKING[mode]

        if time_limit_seconds is None:
            time_limit_seconds = (
                count * DEFAULT_SECONDS_PER_QUESTION if mode is QuizMode.MOCK_TEST else 0
            )

        quiz = Quiz(
            title=title or _default_title(count, mode, topic, difficulty),
            mode=mode.value,
            status=QuizStatus.IN_PROGRESS.value,
            topic=topic or "",
            difficulty=difficulty or "",
            question_ids=[q.id for q in questions],
            time_limit_seconds=max(0, time_limit_seconds),
            started_at=datetime.now(UTC),
            marks_correct=marks_correct,
            marks_wrong=marks_wrong,
            max_score=float(count * marks_correct),
            # A row per question up front, so an unanswered question is a real
            # record with `is_correct = NULL` rather than an absence the grader
            # has to infer.
            #
            # Assigned through the relationship rather than by setting quiz_id
            # on separately-added rows. Both produce the same inserts, but only
            # this populates `quiz.answers` in memory — otherwise the caller's
            # first read of it triggers a lazy load outside the async context
            # and raises MissingGreenlet.
            answers=[
                QuizAnswer(question_id=question.id, position=position)
                for position, question in enumerate(questions)
            ],
        )
        self.db.add(quiz)
        await self.db.flush()
        return quiz

    # ── reading ───────────────────────────────────────────────────────────

    async def get(self, quiz_id: int) -> Quiz | None:
        return await self.db.get(Quiz, quiz_id)

    async def list(self, *, limit: int = 50, offset: int = 0) -> list[Quiz]:
        return list(
            (
                await self.db.execute(
                    select(Quiz).order_by(Quiz.id.desc()).limit(limit).offset(offset)
                )
            )
            .scalars()
            .all()
        )

    async def questions_for(self, quiz: Quiz) -> dict[int, PracticeQuestion]:
        """The quiz's questions, keyed by id.

        Returns a mapping rather than a list because the caller pairs them
        with answer rows, and a question deleted from the bank should be an
        absent key rather than a silently shorter list.
        """
        if not quiz.question_ids:
            return {}
        rows = (
            (
                await self.db.execute(
                    select(PracticeQuestion).where(PracticeQuestion.id.in_(quiz.question_ids))
                )
            )
            .scalars()
            .all()
        )
        return {row.id: row for row in rows}

    # ── answering ─────────────────────────────────────────────────────────

    async def record_answer(
        self,
        quiz_id: int,
        question_id: int,
        *,
        selected: list[int] | None = None,
        written: str | None = None,
        self_marked: bool | None = None,
    ) -> QuizAnswer:
        """Store an answer without grading it.

        Rejects anything after the deadline. Enforcing the clock here, at the
        point of writing, is what makes the limit real — checking it only at
        submit would let a whole paper be answered late.
        """
        quiz = await self.get(quiz_id)
        if quiz is None:
            raise QuizError("quiz not found")

        if quiz.status == QuizStatus.SUBMITTED.value:
            raise QuizError("this quiz has already been submitted")

        if quiz.is_expired():
            quiz.status = QuizStatus.EXPIRED.value
            await self.db.flush()
            raise QuizError("time is up — this quiz can no longer be answered")

        if quiz.status == QuizStatus.EXPIRED.value:
            raise QuizError("time is up — this quiz can no longer be answered")

        answer = (
            await self.db.execute(
                select(QuizAnswer).where(
                    QuizAnswer.quiz_id == quiz_id, QuizAnswer.question_id == question_id
                )
            )
        ).scalar_one_or_none()

        if answer is None:
            raise QuizError("that question is not part of this quiz")

        if selected is not None:
            answer.selected = selected
        if written is not None:
            answer.written = written
        if self_marked is not None:
            answer.self_marked = self_marked
        answer.answered_at = datetime.now(UTC)

        await self.db.flush()
        return answer

    # ── grading ───────────────────────────────────────────────────────────

    async def submit(self, quiz_id: int) -> Quiz:
        """Mark the whole paper and record the result.

        Idempotent: submitting an already-submitted quiz returns the existing
        result rather than re-marking. A double-tapped submit button must not
        produce a second, different score.
        """
        quiz = await self.get(quiz_id)
        if quiz is None:
            raise QuizError("quiz not found")

        if quiz.status == QuizStatus.SUBMITTED.value:
            return quiz

        questions = await self.questions_for(quiz)

        correct = wrong = unattempted = 0
        score = 0.0

        for answer in quiz.answers:
            question = questions.get(answer.question_id)

            if not answer.attempted or question is None:
                # A question whose row vanished from the bank cannot be marked
                # either way, and must not cost the student a negative mark.
                answer.is_correct = None
                answer.marks = 0.0
                unattempted += 1
                continue

            is_correct = _mark(question, answer)
            answer.is_correct = is_correct

            if is_correct:
                answer.marks = float(quiz.marks_correct)
                correct += 1
            else:
                answer.marks = float(quiz.marks_wrong)
                wrong += 1

            score += answer.marks

            # Feed the result back into the bank, so a quiz counts toward the
            # same per-question history that practice does.
            question.attempts += 1
            if is_correct:
                question.correct += 1
            question.last_attempt_at = datetime.now(UTC)

        quiz.correct_count = correct
        quiz.wrong_count = wrong
        quiz.unattempted_count = unattempted
        quiz.score = score
        quiz.elapsed_seconds = quiz.seconds_elapsed()
        quiz.submitted_at = datetime.now(UTC)
        quiz.status = QuizStatus.SUBMITTED.value

        await self.db.flush()
        logger.info(
            "quiz %d submitted: %s/%s (%d correct, %d wrong, %d blank)",
            quiz.id,
            score,
            quiz.max_score,
            correct,
            wrong,
            unattempted,
        )
        return quiz

    async def delete(self, quiz_id: int) -> bool:
        quiz = await self.get(quiz_id)
        if quiz is None:
            return False
        await self.db.delete(quiz)
        await self.db.flush()
        return True

    # ── aggregate ─────────────────────────────────────────────────────────

    async def stats(self) -> dict:
        """Scores over time — what Phase 8 will chart."""
        rows = list(
            (
                await self.db.execute(
                    select(Quiz)
                    .where(Quiz.status == QuizStatus.SUBMITTED.value)
                    .order_by(Quiz.id.desc())
                    .limit(50)
                )
            )
            .scalars()
            .all()
        )

        if not rows:
            return {"quizzes": 0, "average_percent": None, "best_percent": None, "recent": []}

        percents = [
            round(100 * q.score / q.max_score, 1) if q.max_score else 0.0 for q in rows
        ]

        return {
            "quizzes": len(rows),
            "average_percent": round(sum(percents) / len(percents), 1),
            "best_percent": max(percents),
            "recent": [
                {
                    "id": q.id,
                    "title": q.title,
                    "mode": q.mode,
                    "score": q.score,
                    "max_score": q.max_score,
                    "percent": percent,
                    "accuracy": q.accuracy,
                    "submitted_at": q.submitted_at.isoformat() if q.submitted_at else "",
                }
                # Oldest first, so a chart reads left to right.
                for q, percent in reversed(list(zip(rows, percents, strict=True)))
            ],
        }


def _mark(question: PracticeQuestion, answer: QuizAnswer) -> bool:
    """Decide whether one answer is correct.

    Choice questions are marked against the stored key. Written ones cannot be
    — "2x" and "2 x" and "2\\cdot x" are the same answer and no string
    comparison gets that right — so they fall back to the student's own mark,
    and an unmarked written answer counts as wrong rather than silently right.
    """
    body = question.question or {}
    expected = set(body.get("correct_options") or [])

    if expected:
        # Set equality, not containment: on a multiple-correct question,
        # picking one of two right options is not a correct answer.
        return set(answer.selected) == expected

    return bool(answer.self_marked)


def _default_title(
    count: int, mode: QuizMode, topic: str | None, difficulty: str | None
) -> str:
    parts = [str(count)]
    if difficulty:
        parts.append(difficulty.replace("_", " "))
    parts.append("questions" if count != 1 else "question")
    if topic:
        parts.append(f"on {topic.replace('_', ' ')}")
    label = " ".join(parts)
    return f"Mock test — {label}" if mode is QuizMode.MOCK_TEST else label.capitalize()
