"""
Conversation and turn models.
═══════════════════════════════════════════════════════════════════════════

THE SHAPE

    Conversation ──< Turn
         one            many

    A Conversation is a thread. A Turn is one problem and its verified
    solution. Follow-up questions ("why that substitution?", "now do it to
    infinity") land in the same conversation and can see what came before.

WHY THE SOLUTION IS STORED AS JSON, NOT NORMALISED
    A Solution has steps, formulas, mistakes, concepts — normalising it would
    mean five more tables and a join every time a page loads, to support
    queries nobody will run. Nothing in this product asks "find every solution
    whose third step used integration by parts".

    What IS queried is stored as real columns: `problem`, `final_answer`,
    `topic`, `difficulty`, `verified`, `bookmarked`. Those are indexed and
    searchable; the rest is a document.

    This is the pragmatic split, not laziness — normalise what you query, and
    store what you only ever read back whole.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin


class Conversation(Base, TimestampMixin):
    """A thread of related problems."""

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # Auto-generated from the first problem, editable afterwards. Titling a
    # thread is a chore nobody does, so it must never be required.
    title: Mapped[str] = mapped_column(String(200), nullable=False, default="New chat")

    # Denormalised counters. Recomputing them means aggregating every turn on
    # every render of the sidebar; keeping them current costs one increment
    # per solve, which is the better trade for a list that renders constantly.
    turn_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Set whenever a turn is added, so the sidebar can sort by recency without
    # joining. `updated_at` alone would also move on a rename, which is not
    # the same thing.
    last_turn_at: Mapped[Any] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )

    archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    turns: Mapped[list[Turn]] = relationship(
        back_populates="conversation",
        # Deleting a conversation must take its turns with it. Without
        # cascade the rows are orphaned and quietly accumulate forever.
        cascade="all, delete-orphan",
        order_by="Turn.id",
        lazy="selectin",
    )

    __table_args__ = (
        # The sidebar's only query: newest first, hiding archived.
        Index("ix_conversation_recent", "archived", "last_turn_at"),
    )


class Turn(Base, TimestampMixin):
    """One problem and the verified solution to it."""

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    conversation_id: Mapped[int] = mapped_column(
        # ondelete matters as much as the ORM cascade above: the ORM rule only
        # applies when SQLAlchemy loads the parent, whereas this holds for a
        # direct DELETE in psql too.
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    conversation: Mapped[Conversation] = relationship(back_populates="turns")

    # ── what was asked ────────────────────────────────────────────────────
    problem: Mapped[str] = mapped_column(Text, nullable=False)

    # ── the queryable parts of the answer ─────────────────────────────────
    final_answer: Mapped[str] = mapped_column(Text, nullable=False, default="")
    topic: Mapped[str] = mapped_column(String(40), nullable=False, default="other", index=True)
    difficulty: Mapped[str] = mapped_column(String(20), nullable=False, default="medium")

    # The headline fact about any answer, so it is a column rather than being
    # buried in the JSON — "show me everything that failed verification" is a
    # question worth being able to ask.
    verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    verdict_kind: Mapped[str] = mapped_column(String(20), nullable=False, default="error")

    # ── the rest, stored whole ────────────────────────────────────────────
    solution: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    verdict: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    # ── provenance ────────────────────────────────────────────────────────
    # Worth keeping: when an answer looks wrong months later, the first
    # question is which model and tier produced it.
    model: Mapped[str] = mapped_column(String(60), nullable=False, default="")
    tier: Mapped[str] = mapped_column(String(20), nullable=False, default="balanced")
    latency_ms: Mapped[float] = mapped_column(nullable=False, default=0.0)

    # ── the student's own marks ───────────────────────────────────────────
    bookmarked: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, index=True
    )
    bookmarked_at: Mapped[Any] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")

    __table_args__ = (
        # Bookmarks page: only bookmarked rows, newest first.
        Index("ix_turn_bookmarked_at", "bookmarked", "bookmarked_at"),
    )

    def summary(self) -> str:
        """A compact 'problem -> answer' line, for feeding back as context.

        Prior turns are replayed to the model so follow-ups make sense, but
        replaying the FULL solution JSON would spend thousands of tokens per
        turn and push a long thread past the context window. The problem and
        its answer carry the thread; the derivation does not need repeating.
        """
        answer = self.final_answer or "(no answer)"
        return f"Problem: {self.problem}\nAnswer: {answer}"


def touch_conversation(conversation: Conversation) -> None:
    """Update a conversation's counters after a turn is appended."""
    conversation.turn_count += 1
    conversation.last_turn_at = func.now()
