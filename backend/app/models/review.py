"""
Stored reviews of student working.
═══════════════════════════════════════════════════════════════════════════

WHY REVIEWS ARE KEPT
    One review tells a student where this attempt went wrong. Fifty reviews
    tell them something they cannot see for themselves: that two thirds of
    their errors are sign slips, or that every conceptual mistake is in
    integration by parts.

    That is the input Phase 8 needs, and it can only be built from history.

WHY error_types IS A DENORMALISED STRING
    The queryable question is "how often does this student make each kind of
    mistake", which is a count over a small fixed vocabulary. A mistakes table
    joined per review would answer it, and would also be a table nothing else
    ever reads.

    A comma-separated list of the error types found is enough to aggregate in
    Python over a few hundred rows, and the full mistake objects are in the
    JSON document beside it for rendering. If the volume ever makes that
    aggregation slow, the column is still the right source to build an index
    or a real table from.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import JSON, Boolean, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class ReviewRecord(Base, TimestampMixin):
    """One review of one student attempt."""

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # ── what was submitted ────────────────────────────────────────────────
    problem: Mapped[str] = mapped_column(Text, nullable=False)
    working: Mapped[str] = mapped_column(Text, nullable=False)

    # ── what came back ────────────────────────────────────────────────────
    verdict: Mapped[str] = mapped_column(
        String(30), nullable=False, default="unclear", index=True
    )
    topic: Mapped[str] = mapped_column(String(40), nullable=False, default="other", index=True)
    difficulty: Mapped[str] = mapped_column(String(20), nullable=False, default="medium")

    mistake_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: Comma-separated ErrorType values, e.g. "sign,algebraic". See the module
    #: docstring for why this is not a join table.
    error_types: Mapped[str] = mapped_column(String(200), nullable=False, default="")

    # ── whether the review itself was checked ─────────────────────────────
    # True only when SymPy confirmed the reviewer's own reference answer. A
    # review without this is one model's opinion, and is shown as such.
    verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)

    # Set when SymPy contradicted the reviewer's verdict and it was corrected.
    # Worth storing: a rising rate here means the reviewing prompt is drifting.
    overridden_from: Mapped[str] = mapped_column(String(30), nullable=False, default="")

    # ── the rest, stored whole ────────────────────────────────────────────
    review: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    verdicts: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    model: Mapped[str] = mapped_column(String(60), nullable=False, default="")
    latency_ms: Mapped[float] = mapped_column(nullable=False, default=0.0)

    __table_args__ = (
        # The Phase 8 query: this student's mistakes on a topic, over time.
        Index("ix_review_topic_created", "topic", "created_at"),
    )

    def error_type_list(self) -> list[str]:
        return [t for t in self.error_types.split(",") if t]
