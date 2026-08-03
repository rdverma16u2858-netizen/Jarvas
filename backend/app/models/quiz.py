"""
Quizzes and mock tests.
═══════════════════════════════════════════════════════════════════════════

THE SHAPE

    Quiz ──< QuizAnswer >── PracticeQuestion
     one        many            (by id, not by relationship)

    A Quiz is a fixed selection of questions from the Phase 5 bank, a clock,
    and a marking scheme. A QuizAnswer is what the student put for one of
    them.

WHY THE CLOCK LIVES ON THE SERVER
    `started_at` and `time_limit_seconds` are stored here and the remaining
    time is computed from them on every request. The client is never the
    authority.

    Not because a single-user app has cheating to prevent — because a browser
    tab that sleeps, a laptop that suspends, or a page reload would otherwise
    lose or distort the timer. A mock test whose clock resets when you refresh
    is not a mock test.

WHY QUESTIONS ARE REFERENCED BY ID RATHER THAN COPIED
    The question and its answer key already live in `practice_questions`,
    verified. Copying them into the quiz would mean two sources for the same
    answer, and the copy would be the one nobody re-verified.

    The cost is that deleting a question from the bank leaves a quiz pointing
    at nothing. `question_ids` is stored alongside so a past result can still
    say how many questions there were, and the answer rows carry the marks
    they earned — a graded result stays readable even if the questions go.

WHY ANSWERS ARE STORED UNGRADED UNTIL SUBMIT
    Grading on each answer would mean either revealing the result immediately
    — which ends the quiz as a quiz — or computing and hiding it, which is the
    same work with a chance of leaking. Marking happens once, at submit, over
    the whole paper.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin


class QuizMode(str, Enum):
    """Practice is untimed and forgiving; a mock test is neither."""

    PRACTICE = "practice"
    MOCK_TEST = "mock_test"


class QuizStatus(str, Enum):
    IN_PROGRESS = "in_progress"
    SUBMITTED = "submitted"
    EXPIRED = "expired"


#: Marks per outcome, by mode.
#:
#: The mock-test scheme is JEE's: +4 for a correct answer, -1 for a wrong one,
#: 0 for one left blank. The negative mark is the whole point of practising
#: under it — it makes "answer everything and hope" a losing strategy, and
#: learning when NOT to attempt is a real exam skill that an unmarked quiz
#: cannot teach.
MARKING: dict[QuizMode, tuple[int, int]] = {
    QuizMode.PRACTICE: (1, 0),
    QuizMode.MOCK_TEST: (4, -1),
}

#: Default seconds per question when a mock test does not specify a limit.
#: Three minutes is roughly JEE Advanced's pace.
DEFAULT_SECONDS_PER_QUESTION = 180


class Quiz(Base, TimestampMixin):
    """One quiz or mock test."""

    # The automatic convention derives "quizes" from `Quiz` — its
    # pluralisation rule appends "es" to a trailing z rather than doubling it.
    # Spelled out here because a misspelt table name is forever.
    __tablename__ = "quizzes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    title: Mapped[str] = mapped_column(String(200), nullable=False, default="Quiz")
    mode: Mapped[str] = mapped_column(String(20), nullable=False, default="practice")
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="in_progress", index=True
    )

    # What it was drawn from — kept for the history list, which wants to say
    # "20 hard integrals" without loading every question.
    topic: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    difficulty: Mapped[str] = mapped_column(String(20), nullable=False, default="")

    #: The questions, in order. Duplicated from the answer rows so a result
    #: still knows how long the paper was after a question is deleted.
    question_ids: Mapped[list[int]] = mapped_column(JSON, nullable=False, default=list)

    # ── the clock ─────────────────────────────────────────────────────────
    # 0 means untimed. Stored in seconds rather than as a deadline so the
    # limit survives a quiz being created and started at different moments.
    time_limit_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    started_at: Mapped[Any] = mapped_column(DateTime(timezone=True), nullable=True)
    submitted_at: Mapped[Any] = mapped_column(DateTime(timezone=True), nullable=True)

    # ── the marking scheme, frozen at creation ────────────────────────────
    # Copied rather than looked up from MARKING at grading time: changing the
    # scheme later must not silently restate what a past paper scored.
    marks_correct: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    marks_wrong: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # ── the result, filled in at submit ───────────────────────────────────
    score: Mapped[float] = mapped_column(nullable=False, default=0.0)
    max_score: Mapped[float] = mapped_column(nullable=False, default=0.0)
    correct_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    wrong_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    unattempted_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    elapsed_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    answers: Mapped[list[QuizAnswer]] = relationship(
        back_populates="quiz",
        cascade="all, delete-orphan",
        order_by="QuizAnswer.position",
        lazy="selectin",
    )

    __table_args__ = (Index("ix_quiz_recent", "status", "created_at"),)

    # ── the clock, as behaviour rather than raw columns ───────────────────

    @property
    def timed(self) -> bool:
        return self.time_limit_seconds > 0

    def seconds_remaining(self, *, now: datetime | None = None) -> int | None:
        """Seconds left, or None when the quiz is untimed.

        Clamped at zero: a negative number here would reach the UI as a
        count-up, which reads as the timer being broken rather than expired.
        """
        if not self.timed or self.started_at is None:
            return None
        return max(0, self.time_limit_seconds - self.seconds_elapsed(now=now))

    def seconds_elapsed(self, *, now: datetime | None = None) -> int:
        if self.started_at is None:
            return 0
        started = self.started_at
        # SQLite hands back naive datetimes even from a timezone=True column,
        # so the stored value is treated as UTC rather than compared against
        # an aware `now` and raising.
        if started.tzinfo is None:
            started = started.replace(tzinfo=UTC)
        return int(((now or datetime.now(UTC)) - started).total_seconds())

    def is_expired(self, *, now: datetime | None = None) -> bool:
        if self.status != QuizStatus.IN_PROGRESS.value:
            return False
        remaining = self.seconds_remaining(now=now)
        return remaining is not None and remaining <= 0

    @property
    def accuracy(self) -> float | None:
        """Correct as a fraction of ATTEMPTED, not of the whole paper.

        Two different students both scoring 40% — one who attempted
        everything and one who attempted a quarter and got it all right —
        have opposite problems. Accuracy separates them; the score does not.
        """
        attempted = self.correct_count + self.wrong_count
        return round(self.correct_count / attempted, 3) if attempted else None


class QuizAnswer(Base, TimestampMixin):
    """What the student put for one question, and what it earned."""

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    quiz_id: Mapped[int] = mapped_column(
        ForeignKey("quizzes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    quiz: Mapped[Quiz] = relationship(back_populates="answers")

    #: The question this answers. Deliberately NOT a foreign key: a graded
    #: paper must survive its questions being deleted from the bank.
    question_id: Mapped[int] = mapped_column(Integer, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # ── what they put ─────────────────────────────────────────────────────
    #: Chosen option indices, for choice questions.
    selected: Mapped[list[int]] = mapped_column(JSON, nullable=False, default=list)
    #: Free text, for written questions.
    written: Mapped[str] = mapped_column(Text, nullable=False, default="")
    #: The student's own mark for a written answer. None until they give one.
    self_marked: Mapped[bool | None] = mapped_column(nullable=True, default=None)

    answered_at: Mapped[Any] = mapped_column(DateTime(timezone=True), nullable=True)

    # ── what it earned, filled in at submit ───────────────────────────────
    #: None while the quiz is running — ungraded, not "not yet correct".
    is_correct: Mapped[bool | None] = mapped_column(nullable=True, default=None)
    marks: Mapped[float] = mapped_column(nullable=False, default=0.0)

    @property
    def attempted(self) -> bool:
        return bool(self.selected) or bool(self.written.strip())
