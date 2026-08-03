"""
Mistake detection — review a student's own working.
═══════════════════════════════════════════════════════════════════════════

    POST   /review              review an attempt
    GET    /review/patterns     what this student gets wrong, aggregated
    GET    /review/health       how often SymPy had to correct the reviewer
    GET    /review/history      past reviews
    GET    /review/{id}         one review
    DELETE /review/{id}

WHY THE RESPONSE LEADS WITH `student_was_right`
    It is the question the student actually asked. Everything else — the
    mistakes, the corrected working, the concept to revise — is the answer to
    "why", and is useless if the "whether" is buried or, worse, wrong.

    The field is a nullable boolean on purpose. `null` means SymPy could not
    settle it, which is a genuinely different state from "no" and must not be
    rendered as one.

WHY `overridden_from` IS IN THE RESPONSE AT ALL
    When SymPy contradicts the reviewer and the verdict is corrected, the
    client is told. Silently rewriting a judgement would make the system less
    inspectable, and the correction is the most interesting thing that can
    happen in this endpoint.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.ratelimit import llm_rate_limit
from app.db.session import get_db
from app.llm.base import ModelTier
from app.llm.errors import ConfigurationError, RateLimitError
from app.llm.factory import get_provider
from app.math.review import Review
from app.math.reviewer import MAX_WORKING_CHARS, Reviewer, ReviewError
from app.models.review import ReviewRecord
from app.services.reviews import ReviewService

logger = get_logger(__name__)

router = APIRouter(prefix="/review", tags=["review"])


# ── request and response shapes ───────────────────────────────────────────


class ReviewRequest(BaseModel):
    problem: str = Field(
        min_length=2,
        max_length=6000,
        description="The problem the student was attempting",
        examples=["Evaluate the integral of x*exp(x) dx"],
    )
    working: str = Field(
        min_length=1,
        max_length=MAX_WORKING_CHARS,
        description="The student's own working, one step per line",
        examples=["Let u = x, dv = e^x dx\ndu = dx, v = e^x\n= x e^x - e^x + C"],
    )
    tier: ModelTier = Field(default=ModelTier.BALANCED)
    save: bool = Field(default=True, description="False reviews without recording anything")


class VerdictOut(BaseModel):
    kind: str
    detail: str = ""
    expected: str = ""
    claimed: str = ""
    checks: list[str] = Field(default_factory=list)


class ReviewResponse(BaseModel):
    # ── the headline ──────────────────────────────────────────────────────
    #: null when SymPy could not settle it — NOT the same as False.
    student_was_right: bool | None = Field(
        description="True/False when SymPy could determine it, null when it could not"
    )
    verdict: str
    #: True only when SymPy confirmed the reviewer's own reference answer.
    verified: bool = Field(
        description="Whether the reference answer this review is built on was itself checked"
    )
    #: Present when SymPy contradicted the reviewer and the verdict was fixed.
    overridden_from: str | None = None

    review: Review

    answer_check: VerdictOut
    student_check: VerdictOut

    review_id: int | None = None
    model: str = ""
    total_ms: float = 0.0


class ReviewSummary(BaseModel):
    id: int
    problem: str
    verdict: str
    topic: str
    difficulty: str
    mistake_count: int
    error_types: list[str]
    verified: bool
    overridden_from: str
    created_at: str

    @classmethod
    def of(cls, row: ReviewRecord) -> "ReviewSummary":
        return cls(
            id=row.id,
            problem=row.problem,
            verdict=row.verdict,
            topic=row.topic,
            difficulty=row.difficulty,
            mistake_count=row.mistake_count,
            error_types=row.error_type_list(),
            verified=row.verified,
            overridden_from=row.overridden_from,
            created_at=row.created_at.isoformat() if row.created_at else "",
        )


class ReviewDetail(ReviewSummary):
    working: str
    review: dict
    verdicts: dict


# ── reviewing ─────────────────────────────────────────────────────────────


@router.post(
    "",
    dependencies=[Depends(llm_rate_limit)],
    response_model=ReviewResponse,
    summary="Review a student's working and find the first mistake",
    responses={
        429: {"description": "Provider rate limit"},
        502: {"description": "The model could not be reached or gave unusable output"},
        503: {"description": "No LLM provider configured"},
    },
)
async def review(request: ReviewRequest, db: AsyncSession = Depends(get_db)) -> ReviewResponse:
    """Review an attempt, then check the review itself against SymPy.

    The reviewer's verdict is a claim, not a ruling. If SymPy confirms the
    reference answer and confirms the student matched it, a verdict of "wrong"
    is overridden — telling a student their correct work is wrong is the one
    outcome this endpoint must not produce.
    """
    try:
        provider = get_provider()
    except ConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc

    try:
        result = await Reviewer(provider).review(
            request.problem, request.working, tier=request.tier
        )
    except RateLimitError as exc:
        headers = {"Retry-After": str(int(exc.retry_after))} if exc.retry_after else None
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                "Daily quota exhausted — resets tomorrow."
                if exc.daily
                else f"Rate limited. {exc.message}"
            ),
            headers=headers,
        ) from exc
    except ReviewError as exc:
        logger.exception("review failed")
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    review_id: int | None = None

    if request.save:
        # A failed save must not lose the review the student is waiting for.
        try:
            row = await ReviewService(db).save(request.problem, request.working, result)
            await db.commit()
            review_id = row.id
        except Exception:  # noqa: BLE001
            logger.exception("failed to save the review - returning it unsaved")
            await db.rollback()

    return ReviewResponse(
        student_was_right=result.student_was_right,
        verdict=result.review.verdict.value,
        verified=result.verified,
        overridden_from=(result.overridden_from.value if result.overridden_from else None),
        review=result.review,
        answer_check=VerdictOut(**result.answer_verdict.__dict__),
        student_check=VerdictOut(**result.student_verdict.__dict__),
        review_id=review_id,
        model=result.model,
        total_ms=result.total_ms,
    )


# ── literal paths — MUST precede /{review_id} ─────────────────────────────


@router.get("/patterns", summary="What this student gets wrong, aggregated")
async def patterns(db: AsyncSession = Depends(get_db)) -> dict:
    """One review is feedback; fifty are a pattern the student cannot see."""
    return await ReviewService(db).mistake_patterns()


@router.get("/health", summary="How often SymPy had to correct the reviewer")
async def health(db: AsyncSession = Depends(get_db)) -> dict:
    """Not student-facing. A rising override rate means the reviewing prompt
    is drifting toward marking correct work wrong."""
    return await ReviewService(db).override_rate()


@router.get("/history", response_model=list[ReviewSummary], summary="Past reviews")
async def history(
    topic: str | None = None,
    verdict: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> list[ReviewSummary]:
    rows = await ReviewService(db).list(
        topic=topic, verdict=verdict, limit=limit, offset=offset
    )
    return [ReviewSummary.of(row) for row in rows]


# ── one review ────────────────────────────────────────────────────────────


@router.get(
    "/{review_id}",
    response_model=ReviewDetail,
    summary="One review in full",
    responses={404: {"description": "No such review"}},
)
async def get_review(review_id: int, db: AsyncSession = Depends(get_db)) -> ReviewDetail:
    row = await ReviewService(db).get(review_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Review not found")

    return ReviewDetail(
        **ReviewSummary.of(row).model_dump(),
        working=row.working,
        review=row.review,
        verdicts=row.verdicts,
    )


@router.delete(
    "/{review_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a review",
    responses={404: {"description": "No such review"}},
)
async def delete_review(review_id: int, db: AsyncSession = Depends(get_db)) -> None:
    if not await ReviewService(db).delete(review_id):
        raise HTTPException(status_code=404, detail="Review not found")
    await db.commit()
