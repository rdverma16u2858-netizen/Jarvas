"""
Progress tracking and adaptive difficulty.
═══════════════════════════════════════════════════════════════════════════

    GET  /progress          the whole picture, in one request
    GET  /progress/next     the single next thing to do
    GET  /progress/topics   per-topic breakdown only
    GET  /progress/ladder   the difficulty ordering this uses

WHY /progress IS ONE FAT REQUEST
    The page shows overall figures, a per-topic table, a quiz trend and an
    error breakdown, all at once. Four endpoints would mean four round trips
    to draw one screen, four loading states, and a page that assembles itself
    in pieces. The aggregation is cheap; the request count is what would be
    felt.

WHY /progress/ladder IS PUBLISHED
    The difficulty ordering is a judgement — `university` is a separate track
    rather than the top rung, and `jee_main` sits below `hard`. A student
    being told to "move up" deserves to be able to see what up means, and a
    client rendering the ladder should not hardcode a second opinion about it.
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.session import get_db
from app.services.progress import (
    LADDER,
    MIN_ATTEMPTS_FOR_ADJUSTMENT,
    MIN_ATTEMPTS_FOR_SIGNAL,
    TOO_EASY,
    TOO_HARD,
    UNRANKED,
    ProgressService,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/progress", tags=["progress"])


class LadderOut(BaseModel):
    """The difficulty ordering, and the thresholds that act on it."""

    ladder: list[str]
    unranked: list[str]
    min_attempts_for_signal: int
    min_attempts_for_adjustment: int
    too_easy_above: float
    too_hard_below: float


@router.get("", summary="Everything the progress page needs")
async def overview(db: AsyncSession = Depends(get_db)) -> dict:
    """Overall figures, per-topic mastery, quiz trend, error breakdown, and
    the single thing worth doing next."""
    return await ProgressService(db).overview()


@router.get("/next", summary="The next thing to do")
async def next_step(db: AsyncSession = Depends(get_db)) -> dict:
    """A concrete instruction — topic, difficulty, and why.

    Returns `action: "start"` when there is not yet enough practice to base a
    recommendation on. That is a real answer, not a failure: advice built on
    four questions is a coin toss wearing the costume of insight.
    """
    return await ProgressService(db).next_step()


@router.get("/topics", summary="Per-topic breakdown")
async def topics(db: AsyncSession = Depends(get_db)) -> list[dict]:
    return [t.__dict__ for t in await ProgressService(db).by_topic()]


@router.get("/ladder", response_model=LadderOut, summary="The difficulty ordering")
async def ladder() -> LadderOut:
    """Published rather than duplicated in the frontend, for the same reason
    as /generate/topics: one source for a judgement the client renders."""
    return LadderOut(
        ladder=[d.value for d in LADDER],
        unranked=sorted(d.value for d in UNRANKED),
        min_attempts_for_signal=MIN_ATTEMPTS_FOR_SIGNAL,
        min_attempts_for_adjustment=MIN_ATTEMPTS_FOR_ADJUSTMENT,
        too_easy_above=TOO_EASY,
        too_hard_below=TOO_HARD,
    )
