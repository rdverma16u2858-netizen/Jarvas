"""
The practice question bank.
═══════════════════════════════════════════════════════════════════════════

WHY GENERATED QUESTIONS ARE STORED AT ALL
    Three reasons, in order of weight:

    1. Repetition. Without a record of what a student has already been given,
       every request for "five medium integrals" returns the same five
       textbook favourites. The stored prompts are fed back as `avoid`.
    2. Cost. Generation is the most expensive call in the product — a set of
       ten is far more output than one solution. Throwing it away after one
       render means paying again to see the same questions.
    3. Phases 7 and 8. A quiz is a selection from a bank, and adaptive
       difficulty needs to know which questions were answered and how.

WHY attempts AND correct LIVE HERE RATHER THAN IN A SEPARATE TABLE
    An attempt history table would let you replay a student's whole path
    through one question. Nothing in this product asks for that — what gets
    asked is "have I got this right yet" and "which topics am I weak in",
    both of which are counters. Two integers answer them without a join.

    If per-attempt review is ever needed, the counters stay valid and an
    attempts table can be added beside them.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class PracticeQuestion(Base, TimestampMixin):
    """One generated practice question and the student's progress on it."""

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # ── what it is ────────────────────────────────────────────────────────
    # Queried constantly ("give me hard integrals I have not seen"), so these
    # are real columns rather than fields inside the JSON document.
    topic: Mapped[str] = mapped_column(String(40), nullable=False, default="other", index=True)
    difficulty: Mapped[str] = mapped_column(
        String(20), nullable=False, default="medium", index=True
    )
    type: Mapped[str] = mapped_column(String(30), nullable=False, default="short_answer")

    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # ── whether the answer key can be trusted ─────────────────────────────
    # The single most important fact about a generated question, so it is a
    # column: a quiz must be able to select confirmed questions only.
    verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    verdict_kind: Mapped[str] = mapped_column(String(20), nullable=False, default="error")

    # ── the rest, stored whole ────────────────────────────────────────────
    # Options, solution outline, hint, concepts, verification claim. Read back
    # as one document to render the question; never queried field by field.
    question: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    verdict: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    # ── provenance ────────────────────────────────────────────────────────
    model: Mapped[str] = mapped_column(String(60), nullable=False, default="")

    # ── the student's progress ────────────────────────────────────────────
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    correct: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_attempt_at: Mapped[Any] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    bookmarked: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, index=True
    )

    __table_args__ = (
        # The selection query for both practice and quizzes: questions on a
        # topic, at a difficulty, whose answer key is confirmed.
        Index("ix_question_selection", "topic", "difficulty", "verified"),
        # "What have I not attempted yet" — the default practice feed.
        Index("ix_question_unattempted", "topic", "attempts"),
    )

    @property
    def unattempted(self) -> bool:
        return self.attempts == 0
