"""
Progress tracking and adaptive difficulty.
═══════════════════════════════════════════════════════════════════════════

WHY THERE IS NO progress TABLE
    Everything here is derived from what Phases 4-7 already record: the
    question bank's attempt counters, the review history's error types, and
    the quiz results. A snapshot table would be a second copy of the same
    facts that could disagree with the first, and the only thing it would buy
    is a faster query on a dataset of a few thousand rows.

    The cost of that choice is honest and worth stating: per-attempt history
    is NOT stored (see models/question.py), so practice trend is coarser than
    quiz trend. Quiz scores and reviews are timestamped and give a real curve;
    practice accuracy is a running total with no shape over time.

WHY THE RECOMMENDATIONS REFUSE TO GUESS
    "You are ready for olympiad problems" after three questions is noise
    wearing the costume of insight, and a student who follows it lands on
    problems they cannot do and concludes they are worse than they are.

    Every recommendation here has a minimum sample size and says "not enough
    yet" below it — the same rule as Phase 6's `most_common_error`. A tutor
    that admits it does not know is more useful than one that always has an
    opinion.

WHY MASTERY IS BANDS AND NOT A SCORE
    A single 0-100 "mastery score" from a weighted formula cannot be argued
    with, because nobody can see inside it. Bands are coarse, but a student
    can be told exactly why they are in one: how many they attempted and what
    fraction they got right. Explainable beats precise here.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.math.schema import Difficulty
from app.models.conversation import Turn
from app.models.question import PracticeQuestion
from app.models.quiz import Quiz, QuizStatus
from app.models.review import ReviewRecord

logger = get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────────
#  The difficulty ladder
# ─────────────────────────────────────────────────────────────────────────
#
# The `Difficulty` enum's declaration order is NOT a difficulty ordering, and
# using it as one is the obvious mistake here.
#
#   · `university` is not harder than `olympiad` — it is a different syllabus
#     (real analysis, abstract algebra at degree level). Promoting an
#     olympiad student "up" to it would move them sideways into unfamiliar
#     material and read as a demotion.
#   · `jee_main` sits below `hard`: it is a moderate paper, whereas "hard"
#     here means a demanding textbook problem.
#
# So the ladder is written out, and anything not on it is unranked.
LADDER: tuple[Difficulty, ...] = (
    Difficulty.EASY,
    Difficulty.MEDIUM,
    Difficulty.JEE_MAIN,
    Difficulty.HARD,
    Difficulty.JEE_ADVANCED,
    Difficulty.OLYMPIAD,
)

#: Difficulties that are a separate track rather than a rung. A student
#: working here is kept here — their level is reported, never "adjusted".
UNRANKED: frozenset[Difficulty] = frozenset({Difficulty.UNIVERSITY})


def rung(difficulty: str) -> int | None:
    """Position on the ladder, or None for an unranked difficulty."""
    try:
        value = Difficulty(difficulty)
    except ValueError:
        return None
    return LADDER.index(value) if value in LADDER else None


def step(difficulty: str, direction: int) -> str:
    """Move one rung, clamped at both ends. Unranked stays put."""
    position = rung(difficulty)
    if position is None:
        return difficulty
    return LADDER[max(0, min(len(LADDER) - 1, position + direction))].value


# ─────────────────────────────────────────────────────────────────────────
#  Thresholds
# ─────────────────────────────────────────────────────────────────────────

#: Attempts on a topic before its accuracy is treated as meaningful at all.
#: Below this the topic is reported as "learning", with no recommendation.
MIN_ATTEMPTS_FOR_SIGNAL = 5

#: Attempts before a difficulty change is suggested. Higher than the signal
#: threshold on purpose: telling someone to move up is a stronger claim than
#: telling them how they are doing, and deserves more evidence.
MIN_ATTEMPTS_FOR_ADJUSTMENT = 8

#: Accuracy at or above which the work is too easy, and below which it is too
#: hard. The gap between them is the band where the difficulty is about right
#: — deliberately wide, so a student is not bounced up and down by a couple of
#: unlucky questions.
TOO_EASY = 0.85
TOO_HARD = 0.45

#: How far back "recent" reaches for the activity summary.
RECENT_DAYS = 14


class Mastery:
    """The bands a topic can be in. Ordered from least to most practised."""

    UNTOUCHED = "untouched"
    LEARNING = "learning"
    DEVELOPING = "developing"
    SOLID = "solid"
    STRONG = "strong"


def band(attempts: int, accuracy: float | None) -> str:
    """Which mastery band an attempt count and accuracy fall into.

    Volume gates accuracy rather than being averaged with it: three questions
    answered correctly is not evidence of mastery, and any formula that lets a
    small sample reach the top band will report mastery a student does not
    have.
    """
    if attempts == 0:
        return Mastery.UNTOUCHED
    if attempts < MIN_ATTEMPTS_FOR_SIGNAL or accuracy is None:
        return Mastery.LEARNING
    if accuracy < 0.7:
        return Mastery.DEVELOPING
    if accuracy < TOO_EASY:
        return Mastery.SOLID
    return Mastery.STRONG


@dataclass
class TopicProgress:
    topic: str
    questions: int
    attempts: int
    correct: int
    accuracy: float | None
    mastery: str
    #: The difficulty most of the practice on this topic sat at.
    working_at: str
    #: What to work at next, or None when there is not enough evidence.
    suggested: str | None
    reason: str
    mistakes: int
    common_error: str | None
    last_seen: str | None


class ProgressService:
    """Reads the signals Phases 4-7 record and turns them into a picture."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # ── per topic ─────────────────────────────────────────────────────────

    async def by_topic(self) -> list[TopicProgress]:
        """One row per topic the student has touched, strongest evidence first."""
        practice = await self._practice_by_topic()
        errors = await self._errors_by_topic()

        rows: list[TopicProgress] = []
        for topic, stats in practice.items():
            attempts = stats["attempts"]
            accuracy = stats["accuracy"]
            working_at = stats["working_at"]
            suggested, reason = self._advise(attempts, accuracy, working_at)

            error_counts = errors.get(topic, Counter())

            rows.append(
                TopicProgress(
                    topic=topic,
                    questions=stats["questions"],
                    attempts=attempts,
                    correct=stats["correct"],
                    accuracy=accuracy,
                    mastery=band(attempts, accuracy),
                    working_at=working_at,
                    suggested=suggested,
                    reason=reason,
                    mistakes=sum(error_counts.values()),
                    common_error=(
                        error_counts.most_common(1)[0][0]
                        if sum(error_counts.values()) >= 3
                        else None
                    ),
                    last_seen=stats["last_seen"],
                )
            )

        # Most-practised first: the topics with the most evidence behind them
        # are the ones whose numbers mean something.
        rows.sort(key=lambda r: (-r.attempts, r.topic))
        return rows

    @staticmethod
    def _advise(
        attempts: int, accuracy: float | None, working_at: str
    ) -> tuple[str | None, str]:
        """Recommend a difficulty, or decline to.

        Returning None is a real answer and the common one early on. A
        recommendation built on four questions would be a coin toss presented
        as advice.
        """
        if rung(working_at) is None:
            return None, "This is a separate track, so there is no level to move between."

        if attempts < MIN_ATTEMPTS_FOR_ADJUSTMENT or accuracy is None:
            needed = MIN_ATTEMPTS_FOR_ADJUSTMENT - attempts
            return None, (
                f"{needed} more {'question' if needed == 1 else 'questions'} "
                "before there is enough to judge the level."
            )

        percent = round(accuracy * 100)

        if accuracy >= TOO_EASY:
            harder = step(working_at, +1)
            if harder == working_at:
                return None, (
                    f"{percent}% at the top of the ladder — there is nothing "
                    "harder to move up to."
                )
            return harder, (
                f"{percent}% correct at {working_at.replace('_', ' ')}. "
                "This is comfortable; the next level up will teach you more."
            )

        if accuracy < TOO_HARD:
            easier = step(working_at, -1)
            if easier == working_at:
                return None, (
                    f"{percent}% at the easiest level — the gap is in the "
                    "material rather than the difficulty. Work through the "
                    "solutions rather than more questions."
                )
            return easier, (
                f"{percent}% correct at {working_at.replace('_', ' ')}. "
                "Dropping a level to rebuild the method is faster than "
                "pushing through."
            )

        return working_at, (
            f"{percent}% correct — this level is pitched about right. Stay here."
        )

    async def _practice_by_topic(self) -> dict[str, dict]:
        rows = await self.db.execute(
            select(
                PracticeQuestion.topic,
                func.count(PracticeQuestion.id),
                func.sum(PracticeQuestion.attempts),
                func.sum(PracticeQuestion.correct),
                func.max(PracticeQuestion.last_attempt_at),
            ).group_by(PracticeQuestion.topic)
        )

        levels = await self._working_levels()

        out: dict[str, dict] = {}
        for topic, questions, attempts, correct, last_seen in rows.all():
            attempts = int(attempts or 0)
            correct = int(correct or 0)
            out[topic] = {
                "questions": questions,
                "attempts": attempts,
                "correct": correct,
                "accuracy": round(correct / attempts, 3) if attempts else None,
                "working_at": levels.get(topic, Difficulty.MEDIUM.value),
                "last_seen": last_seen.isoformat() if last_seen else None,
            }
        return out

    async def _working_levels(self) -> dict[str, str]:
        """The difficulty each topic's ATTEMPTED questions mostly sat at.

        Weighted by attempts rather than by how many questions exist: a bank
        holding forty easy questions the student never opened says nothing
        about the level they are working at.

        One grouped query for every topic, resolved in Python. Asking per
        topic would be a query per row of the progress page.
        """
        rows = await self.db.execute(
            select(
                PracticeQuestion.topic,
                PracticeQuestion.difficulty,
                func.sum(PracticeQuestion.attempts),
                func.count(PracticeQuestion.id),
            ).group_by(PracticeQuestion.topic, PracticeQuestion.difficulty)
        )

        # Most-attempted difficulty per topic; where nothing has been
        # attempted, the one the bank holds most of — so the level shown is at
        # least the level available.
        best_attempted: dict[str, tuple[int, str]] = {}
        best_stocked: dict[str, tuple[int, str]] = {}

        for topic, difficulty, attempts, questions in rows.all():
            attempts = int(attempts or 0)
            if attempts > 0 and attempts > best_attempted.get(topic, (0, ""))[0]:
                best_attempted[topic] = (attempts, difficulty)
            if questions > best_stocked.get(topic, (0, ""))[0]:
                best_stocked[topic] = (questions, difficulty)

        return {
            topic: best_attempted.get(topic, best_stocked.get(topic, (0, "")))[1]
            or Difficulty.MEDIUM.value
            for topic in set(best_attempted) | set(best_stocked)
        }

    async def _errors_by_topic(self) -> dict[str, Counter]:
        rows = list((await self.db.execute(select(ReviewRecord))).scalars().all())
        out: dict[str, Counter] = {}
        for row in rows:
            out.setdefault(row.topic, Counter()).update(row.error_type_list())
        return out

    # ── the whole picture ─────────────────────────────────────────────────

    async def overview(self) -> dict:
        """Everything the progress page needs, in one request."""
        topics = await self.by_topic()

        total_attempts = sum(t.attempts for t in topics)
        total_correct = sum(t.correct for t in topics)

        quizzes = await self._quiz_trend()
        errors = await self._error_totals()
        activity = await self._recent_activity()

        return {
            "overall": {
                "topics_touched": len([t for t in topics if t.attempts > 0]),
                "questions_attempted": total_attempts,
                "correct": total_correct,
                "accuracy": (
                    round(total_correct / total_attempts, 3) if total_attempts else None
                ),
                "quizzes_taken": quizzes["count"],
                "average_quiz_percent": quizzes["average"],
                "problems_solved": activity["problems_solved"],
                "reviews": activity["reviews"],
            },
            "topics": [t.__dict__ for t in topics],
            "quiz_trend": quizzes["points"],
            "errors": errors,
            "recent": activity,
            "focus": self._focus(topics),
        }

    @staticmethod
    def _focus(topics: list[TopicProgress]) -> dict | None:
        """The single thing worth doing next.

        A progress page that lists ten topics and no priority leaves the
        student to work out where to start, which is the part they came for.

        The weakest topic with enough evidence behind it wins. Topics that are
        going fine, and topics with too little data to judge, are not
        candidates — recommending either would be guessing.
        """
        candidates = [
            t
            for t in topics
            if t.attempts >= MIN_ATTEMPTS_FOR_SIGNAL
            and t.accuracy is not None
            and t.accuracy < TOO_EASY
        ]
        if not candidates:
            return None

        weakest = min(candidates, key=lambda t: t.accuracy or 1.0)
        return {
            "topic": weakest.topic,
            "difficulty": weakest.suggested or weakest.working_at,
            "accuracy": weakest.accuracy,
            "mastery": weakest.mastery,
            "common_error": weakest.common_error,
            "why": (
                f"{round((weakest.accuracy or 0) * 100)}% on "
                f"{weakest.topic.replace('_', ' ')} — your weakest topic with "
                "enough attempts to be sure."
            ),
        }

    async def _quiz_trend(self) -> dict:
        rows = list(
            (
                await self.db.execute(
                    select(Quiz)
                    .where(Quiz.status == QuizStatus.SUBMITTED.value)
                    .order_by(Quiz.id.desc())
                    .limit(20)
                )
            )
            .scalars()
            .all()
        )

        points = [
            {
                "id": q.id,
                "title": q.title,
                "percent": round(100 * q.score / q.max_score, 1) if q.max_score else 0.0,
                "accuracy": q.accuracy,
                "mode": q.mode,
                "at": q.submitted_at.isoformat() if q.submitted_at else "",
            }
            # Oldest first, so a chart reads left to right.
            for q in reversed(rows)
        ]

        return {
            "count": len(points),
            "average": (
                round(sum(p["percent"] for p in points) / len(points), 1) if points else None
            ),
            "points": points,
        }

    async def _error_totals(self) -> list[dict]:
        rows = list((await self.db.execute(select(ReviewRecord))).scalars().all())
        counts: Counter[str] = Counter()
        for row in rows:
            counts.update(row.error_type_list())
        return [{"type": name, "count": count} for name, count in counts.most_common()]

    async def _recent_activity(self) -> dict:
        since = datetime.now(UTC) - timedelta(days=RECENT_DAYS)

        solved = (await self.db.execute(select(func.count(Turn.id)))).scalar() or 0
        reviews = (await self.db.execute(select(func.count(ReviewRecord.id)))).scalar() or 0
        recent_reviews = (
            await self.db.execute(
                select(func.count(ReviewRecord.id)).where(ReviewRecord.created_at >= since)
            )
        ).scalar() or 0

        return {
            "problems_solved": solved,
            "reviews": reviews,
            "reviews_recent": recent_reviews,
            "window_days": RECENT_DAYS,
        }

    # ── what to do next ───────────────────────────────────────────────────

    async def next_step(self) -> dict:
        """A concrete instruction, or an honest admission that it is too early."""
        topics = await self.by_topic()
        focus = self._focus(topics)

        if focus is None:
            practised = [t for t in topics if t.attempts > 0]
            if not practised:
                return {
                    "action": "start",
                    "topic": None,
                    "difficulty": Difficulty.MEDIUM.value,
                    "message": (
                        "Nothing practised yet. Generate a set of questions on a "
                        "topic you are working on and answer a few — the "
                        "recommendations here need about eight attempts before "
                        "they mean anything."
                    ),
                }

            # Everything with enough data is going well.
            strongest = max(practised, key=lambda t: t.accuracy or 0)
            harder = step(strongest.working_at, +1)
            return {
                "action": "advance",
                "topic": strongest.topic,
                "difficulty": harder,
                "message": (
                    f"Nothing is going badly. {strongest.topic.replace('_', ' ')} "
                    f"is your strongest — try it at "
                    f"{harder.replace('_', ' ')}."
                ),
            }

        message = focus["why"]
        if focus["common_error"]:
            message += (
                f" Most of your mistakes there are "
                f"{focus['common_error'].replace('_', ' ')} errors."
            )

        return {
            "action": "practise",
            "topic": focus["topic"],
            "difficulty": focus["difficulty"],
            "message": message,
        }
