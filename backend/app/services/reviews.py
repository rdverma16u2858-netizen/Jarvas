"""
Review storage and mistake-pattern aggregation.
═══════════════════════════════════════════════════════════════════════════

`mistake_patterns` is the reason this layer exists. A single review is
immediate feedback; the aggregate is the thing a student cannot see for
themselves — that most of their errors are sign slips rather than gaps in
understanding, or that every conceptual mistake sits in one topic.

Aggregated in Python rather than SQL because the error types are a
comma-separated column (see models/review.py for why) and the row count for
one student is small. If that stops being true, the column is still the right
source to build a real index from.
"""

from __future__ import annotations

from collections import Counter

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.math.reviewer import ReviewResult
from app.models.review import ReviewRecord

logger = get_logger(__name__)

#: How many recent reviews the pattern summary looks at. Far enough back to be
#: a pattern, recent enough that improving actually shows up — a student who
#: has fixed their sign errors should stop being told about them.
PATTERN_WINDOW = 50


class ReviewService:
    """All reads and writes for stored reviews."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # ── writing ───────────────────────────────────────────────────────────

    async def save(self, problem: str, working: str, result: ReviewResult) -> ReviewRecord:
        review = result.review
        row = ReviewRecord(
            problem=problem,
            working=working,
            verdict=review.verdict.value,
            topic=review.topic.value,
            difficulty=review.difficulty.value,
            mistake_count=len(review.mistakes),
            error_types=",".join(m.type.value for m in review.mistakes),
            verified=result.verified,
            overridden_from=(result.overridden_from.value if result.overridden_from else ""),
            review=review.model_dump(mode="json"),
            verdicts={
                "answer": {
                    "kind": result.answer_verdict.kind.value,
                    "detail": result.answer_verdict.detail,
                    "expected": result.answer_verdict.expected,
                    "claimed": result.answer_verdict.claimed,
                    "checks": result.answer_verdict.checks,
                },
                "student": {
                    "kind": result.student_verdict.kind.value,
                    "detail": result.student_verdict.detail,
                    "expected": result.student_verdict.expected,
                    "claimed": result.student_verdict.claimed,
                    "checks": result.student_verdict.checks,
                },
            },
            model=result.model,
            latency_ms=result.total_ms,
        )
        self.db.add(row)
        await self.db.flush()
        return row

    # ── reading ───────────────────────────────────────────────────────────

    async def get(self, review_id: int) -> ReviewRecord | None:
        return await self.db.get(ReviewRecord, review_id)

    async def list(
        self,
        *,
        topic: str | None = None,
        verdict: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ReviewRecord]:
        statement = select(ReviewRecord)
        if topic:
            statement = statement.where(ReviewRecord.topic == topic)
        if verdict:
            statement = statement.where(ReviewRecord.verdict == verdict)

        return list(
            (
                await self.db.execute(
                    statement.order_by(ReviewRecord.id.desc()).limit(limit).offset(offset)
                )
            )
            .scalars()
            .all()
        )

    async def delete(self, review_id: int) -> bool:
        row = await self.get(review_id)
        if row is None:
            return False
        await self.db.delete(row)
        await self.db.flush()
        return True

    # ── aggregate ─────────────────────────────────────────────────────────

    async def mistake_patterns(self, *, limit: int = PATTERN_WINDOW) -> dict:
        """What this student gets wrong, and where.

        Returns the error types ranked by frequency, the topics with the most
        fatal mistakes, and how the verdicts break down.
        """
        rows = list(
            (
                await self.db.execute(
                    select(ReviewRecord).order_by(ReviewRecord.id.desc()).limit(limit)
                )
            )
            .scalars()
            .all()
        )

        error_counts: Counter[str] = Counter()
        topic_counts: Counter[str] = Counter()
        verdict_counts: Counter[str] = Counter()

        for row in rows:
            verdict_counts[row.verdict] += 1
            types = row.error_type_list()
            error_counts.update(types)
            if types:
                topic_counts[row.topic] += len(types)

        return {
            "reviews": len(rows),
            "mistakes": sum(error_counts.values()),
            "by_error_type": [
                {"type": name, "count": count} for name, count in error_counts.most_common()
            ],
            "by_topic": [
                {"topic": name, "mistakes": count}
                for name, count in topic_counts.most_common()
            ],
            "by_verdict": [
                {"verdict": name, "count": count}
                for name, count in verdict_counts.most_common()
            ],
            # The single most useful line for a student, or None when there is
            # not yet enough to call it a pattern rather than a coincidence.
            "most_common_error": (
                error_counts.most_common(1)[0][0] if sum(error_counts.values()) >= 3 else None
            ),
        }

    async def override_rate(self) -> dict:
        """How often SymPy had to correct the reviewer.

        Not a student-facing number — it is the health check on the reviewing
        prompt. A rising rate means the model is drifting toward marking work
        wrong that is not, which is the failure this phase exists to prevent.
        """
        total = (await self.db.execute(select(func.count(ReviewRecord.id)))).scalar() or 0
        overridden = (
            await self.db.execute(
                select(func.count(ReviewRecord.id)).where(ReviewRecord.overridden_from != "")
            )
        ).scalar() or 0

        return {
            "reviews": total,
            "overridden": overridden,
            "rate": round(overridden / total, 3) if total else None,
        }
