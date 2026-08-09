"""
Practice question generation and the question bank.
═══════════════════════════════════════════════════════════════════════════

    POST   /generate                  generate a set, verify every answer key
    GET    /generate/questions        browse the bank
    GET    /generate/topics           the 16 topics, 7 difficulties, 6 types
    GET    /generate/stats            per-topic counts and accuracy

    GET    /generate/questions/{id}         one question
    POST   /generate/questions/{id}/attempt record an answer
    POST   /generate/questions/{id}/bookmark
    DELETE /generate/questions/{id}

WHY /topics AND /stats SIT ABOVE /questions/{id}
    Same rule as the conversations router: FastAPI matches in declaration
    order, and a literal path declared after a dynamic one is swallowed by it.

WHY THE ANSWER IS WITHHELD UNTIL THE STUDENT ASKS
    `include_answers=false` is the default on every read. A practice question
    whose answer arrives in the same payload is not practice — the answer is
    one devtools tab away, and more importantly the client would have to be
    trusted to hide it. `GET /questions/{id}?include_answers=true` is the
    deliberate reveal.
"""

import asyncio

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.core.ratelimit import llm_rate_limit
from app.db.session import SessionFactory, get_db
from app.llm.base import ModelTier
from app.llm.errors import ConfigurationError, RateLimitError
from app.llm.factory import get_provider
from app.math.generator import MAX_QUESTIONS, GenerationError, Generator
from app.math.questions import QuestionType
from app.math.schema import Difficulty, Topic
from app.models.question import PracticeQuestion
from app.services.generation_jobs import GenerationJob, generation_jobs
from app.services.questions import QuestionService

logger = get_logger(__name__)

router = APIRouter(prefix="/generate", tags=["practice"])


# ── request and response shapes ───────────────────────────────────────────


class GenerateRequest(BaseModel):
    topic: Topic = Field(description="Which of the 16 topics to draw from")
    difficulty: Difficulty = Field(
        default=Difficulty.MEDIUM, description="One of the 7 difficulty bands"
    )
    type: QuestionType = Field(
        default=QuestionType.MULTIPLE_CHOICE, description="One of the 6 question formats"
    )
    # Keep the default interactive on a free hosted instance. The student can
    # still explicitly request up to MAX_QUESTIONS when they need a longer set.
    count: int = Field(default=3, ge=1, le=MAX_QUESTIONS)
    concepts: str = Field(
        default="",
        max_length=500,
        description="Narrow the set, e.g. 'integration by parts, reduction formulas'",
    )
    # Generating a set is structured content creation, not a proof-solving
    # task. Flash keeps this interactive on free hosting and avoids depending
    # on the more heavily loaded Balanced model for every Practice request.
    tier: ModelTier = Field(default=ModelTier.FAST)
    save: bool = Field(default=True, description="False generates without storing")
    avoid_repeats: bool = Field(
        default=True,
        description="Show the model what has already been generated on this topic",
    )


class QuestionOut(BaseModel):
    """A question as the student receives it — answer fields optional."""

    id: int | None = Field(default=None, description="Null when the set was not saved")
    number: int
    type: str
    topic: str
    difficulty: str
    prompt: str
    options: list[str]
    hint: str
    concepts: list[str]
    time_minutes: int

    # Whether the answer key was independently recomputed. Sent even when the
    # answer itself is withheld: a student deserves to know that the question
    # they are about to spend ten minutes on has an unconfirmed key.
    verified: bool
    verdict_kind: str

    # ── withheld unless asked for ─────────────────────────────────────────
    answer: str | None = None
    answer_latex: str | None = None
    correct_options: list[int] | None = None
    solution_outline: list[str] | None = None

    # ── the student's history on this question ────────────────────────────
    attempts: int = 0
    correct: int = 0
    bookmarked: bool = False

    #: Set only on the response to an attempt: whether THAT attempt was right.
    #: Distinct from `correct`, which is the running total.
    was_correct: bool | None = None

    @classmethod
    def of(cls, row: PracticeQuestion, *, include_answers: bool) -> "QuestionOut":
        body = row.question
        return cls(
            id=row.id,
            number=body.get("number", 0),
            type=row.type,
            topic=row.topic,
            difficulty=row.difficulty,
            prompt=row.prompt,
            options=body.get("options", []),
            hint=body.get("hint", ""),
            concepts=body.get("concepts", []),
            time_minutes=body.get("time_minutes", 5),
            verified=row.verified,
            verdict_kind=row.verdict_kind,
            answer=row.answer if include_answers else None,
            answer_latex=body.get("answer_latex", "") if include_answers else None,
            correct_options=body.get("correct_options", []) if include_answers else None,
            solution_outline=body.get("solution_outline", []) if include_answers else None,
            attempts=row.attempts,
            correct=row.correct,
            bookmarked=row.bookmarked,
        )


class GenerateResponse(BaseModel):
    questions: list[QuestionOut]
    #: How many had their answer key independently recomputed by SymPy.
    confirmed: int
    #: How many were discarded because SymPy contradicted the answer key. A
    #: non-zero value here is the system working, not failing.
    rejected: int
    model: str
    total_ms: float


class GenerationJobOut(BaseModel):
    """The state of an asynchronous practice-generation request."""

    id: str
    status: str
    result: GenerateResponse | None = None
    error: str | None = None
    error_status: int | None = None


class AttemptRequest(BaseModel):
    """How the attempt is graded, which depends on the question format.

    `selected` — the options the student picked. The SERVER grades these.
    `correct`  — the student's own verdict, for written formats a machine
                 cannot mark.

    Send exactly one.
    """

    selected: list[int] | None = Field(
        default=None,
        description="0-based option indices the student chose (choice questions)",
    )
    correct: bool | None = Field(
        default=None,
        description="The student's self-assessment (written questions)",
    )


class BookmarkRequest(BaseModel):
    bookmarked: bool = True


class TopicsResponse(BaseModel):
    """Everything a client needs to build the generation form."""

    topics: list[str]
    difficulties: list[str]
    types: list[str]
    max_count: int


# ── generation ────────────────────────────────────────────────────────────


@router.post(
    "",
    dependencies=[Depends(llm_rate_limit)],
    response_model=GenerateResponse,
    summary="Generate practice questions and verify every answer key",
    responses={
        429: {"description": "Provider rate limit"},
        502: {"description": "The model could not be reached or gave unusable output"},
        503: {"description": "No LLM provider configured"},
        504: {"description": "Generation exceeded the interactive deadline"},
    },
)
async def generate(
    request: GenerateRequest, db: AsyncSession = Depends(get_db)
) -> GenerateResponse:
    """Generate a set, check each answer with SymPy, and store what survives.

    Answers are withheld from this response. The client asks for them one
    question at a time, when the student chooses to reveal.
    """
    try:
        provider = get_provider()
    except ConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc

    service = QuestionService(db)

    avoid = (
        await service.recent_prompts(
            topic=request.topic.value, difficulty=request.difficulty.value
        )
        if request.avoid_repeats
        else None
    )

    try:
        result = await asyncio.wait_for(
            Generator(provider).generate(
                topic=request.topic,
                difficulty=request.difficulty,
                question_type=request.type,
                count=request.count,
                concepts=request.concepts,
                avoid=avoid,
                tier=request.tier,
                # Caching is off by design: "more questions" must be new questions.
                use_cache=False,
            ),
            timeout=settings.GENERATION_TIMEOUT_SECONDS,
        )
    except TimeoutError as exc:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=(
                "Question generation took too long. No questions were saved; please try again."
            ),
        ) from exc
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
    except GenerationError as exc:
        logger.exception("generation failed")
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    questions: list[QuestionOut] = []

    if request.save:
        # A failed save must not lose the questions the student is waiting
        # for, so it is logged and the set is returned unsaved.
        try:
            rows = await service.save_all(result.questions, model=result.model)
            await db.commit()
            questions = [QuestionOut.of(row, include_answers=False) for row in rows]
        except Exception:  # noqa: BLE001
            logger.exception("failed to save the generated set - returning it unsaved")
            await db.rollback()

    if not questions:
        # Either save=False, or the save failed. Build the response straight
        # from the generated objects, without ids.
        questions = [
            QuestionOut(
                id=None,
                number=item.question.number,
                type=item.question.type.value,
                topic=item.question.topic.value,
                difficulty=item.question.difficulty.value,
                prompt=item.question.prompt,
                options=item.question.options,
                hint=item.question.hint,
                concepts=item.question.concepts,
                time_minutes=item.question.time_minutes,
                verified=item.confirmed,
                verdict_kind=item.verdict.kind.value,
            )
            for item in result.questions
        ]

    return GenerateResponse(
        questions=questions,
        confirmed=result.confirmed_count,
        rejected=len(result.rejected),
        model=result.model,
        total_ms=result.total_ms,
    )


def _job_out(job: GenerationJob) -> GenerationJobOut:
    """Keep the in-process job representation out of the HTTP contract."""
    return GenerationJobOut(
        id=job.id,
        status=job.state,
        result=job.result if job.state == "completed" else None,
        error=job.error if job.state == "failed" else None,
        error_status=job.error_status if job.state == "failed" else None,
    )


async def _run_job(request: GenerateRequest) -> GenerateResponse:
    """Run long work with a fresh session after the short POST has returned."""
    async with SessionFactory() as session:
        return await generate(request, session)


@router.post(
    "/jobs",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=GenerationJobOut,
    summary="Start practice generation without holding a browser connection open",
    responses={503: {"description": "No LLM provider configured"}},
)
async def start_generation_job(
    request: GenerateRequest,
    http: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> GenerationJobOut:
    """Queue long generation and return an ID the browser can safely poll.

    The idempotency key is generated once in the browser. If a proxy loses the
    tiny 202 response, its retry receives the same job rather than purchasing
    a second model call or saving duplicate questions.
    """
    key = (idempotency_key or "").strip()
    if not key or len(key) > 200:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="A valid Idempotency-Key header is required.",
        )

    if existing := await generation_jobs.find(key):
        return _job_out(existing)

    try:
        get_provider()
    except ConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc

    # Charge the quota only after idempotency has ruled out a retried POST.
    await llm_rate_limit(http)
    job = await generation_jobs.start(key, lambda: _run_job(request))
    return _job_out(job)


@router.get(
    "/jobs/{job_id}",
    response_model=GenerationJobOut,
    summary="Poll a practice-generation job",
    responses={404: {"description": "Job expired or the server restarted"}},
)
async def get_generation_job(job_id: str) -> GenerationJobOut:
    """Return a small, retry-safe status payload while generation runs."""
    job = await generation_jobs.get(job_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Generation job expired or the server restarted. Please generate again.",
        )
    return _job_out(job)


# ── literal paths — MUST precede /questions/{question_id} ─────────────────


@router.get("/topics", response_model=TopicsResponse, summary="The generation vocabulary")
async def topics() -> TopicsResponse:
    """The valid values for every generation field.

    Served rather than duplicated in the frontend: the enums live in Python,
    and a hardcoded copy in TypeScript drifts the first time one is added.
    """
    return TopicsResponse(
        topics=[t.value for t in Topic],
        difficulties=[d.value for d in Difficulty],
        types=[q.value for q in QuestionType],
        max_count=MAX_QUESTIONS,
    )


@router.get("/stats", summary="Per-topic question counts and accuracy")
async def stats(db: AsyncSession = Depends(get_db)) -> list[dict]:
    return await QuestionService(db).stats_by_topic()


@router.get("/questions", response_model=list[QuestionOut], summary="Browse the bank")
async def list_questions(
    topic: str | None = None,
    difficulty: str | None = None,
    type: str | None = None,
    verified_only: bool = False,
    unattempted_only: bool = False,
    bookmarked_only: bool = False,
    include_answers: bool = False,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> list[QuestionOut]:
    rows = await QuestionService(db).list(
        topic=topic,
        difficulty=difficulty,
        question_type=type,
        verified_only=verified_only,
        unattempted_only=unattempted_only,
        bookmarked_only=bookmarked_only,
        limit=limit,
        offset=offset,
    )
    return [QuestionOut.of(row, include_answers=include_answers) for row in rows]


# ── one question ──────────────────────────────────────────────────────────


@router.get(
    "/questions/{question_id}",
    response_model=QuestionOut,
    summary="One question, optionally with the answer",
    responses={404: {"description": "No such question"}},
)
async def get_question(
    question_id: int,
    include_answers: bool = False,
    db: AsyncSession = Depends(get_db),
) -> QuestionOut:
    """`include_answers=true` is the reveal — the client calls it when the
    student presses "show answer", not before."""
    row = await QuestionService(db).get(question_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Question not found")
    return QuestionOut.of(row, include_answers=include_answers)


@router.post(
    "/questions/{question_id}/attempt",
    response_model=QuestionOut,
    summary="Record an attempt",
    responses={404: {"description": "No such question"}},
)
async def record_attempt(
    question_id: int,
    request: AttemptRequest,
    db: AsyncSession = Depends(get_db),
) -> QuestionOut:
    """Grade an attempt and count it toward the Phase 8 progress figures.

    WHY THE SERVER GRADES CHOICE QUESTIONS
        The client cannot: it has never been sent `correct_options`. Marking
        client-side would mean shipping the answer key to the browser before
        the student commits, which defeats the whole point of withholding it.

        Doing it here also removes a silent failure mode. When the client
        graded, a question whose answer it did not have was scored as WRONG —
        so every multiple-choice attempt was recorded as incorrect and the
        progress figures were quietly meaningless.

    The answer comes back with the response: once an attempt is recorded the
    student has committed, so there is nothing left to withhold.
    """
    service = QuestionService(db)

    row = await service.get(question_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Question not found")

    if request.selected is not None:
        expected = set(row.question.get("correct_options") or [])
        # Set equality, not "contains": on a multiple-correct question,
        # picking one of the two right options is not a correct answer.
        was_correct = set(request.selected) == expected and bool(expected)
    elif request.correct is not None:
        was_correct = request.correct
    else:
        raise HTTPException(
            status_code=422,
            detail="Send either `selected` (choice questions) or `correct` (written).",
        )

    row = await service.record_attempt(question_id, correct=was_correct)
    if row is None:  # pragma: no cover - deleted between the two calls
        raise HTTPException(status_code=404, detail="Question not found")

    await db.commit()

    graded = QuestionOut.of(row, include_answers=True)
    graded.was_correct = was_correct
    return graded


@router.post(
    "/questions/{question_id}/bookmark",
    response_model=QuestionOut,
    summary="Save or unsave a question",
    responses={404: {"description": "No such question"}},
)
async def bookmark_question(
    question_id: int,
    request: BookmarkRequest,
    db: AsyncSession = Depends(get_db),
) -> QuestionOut:
    service = QuestionService(db)
    row = await service.set_bookmark(question_id, request.bookmarked)
    if row is None:
        raise HTTPException(status_code=404, detail="Question not found")
    await db.commit()
    return QuestionOut.of(row, include_answers=False)


@router.delete(
    "/questions/{question_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a question from the bank",
    responses={404: {"description": "No such question"}},
)
async def delete_question(question_id: int, db: AsyncSession = Depends(get_db)) -> None:
    service = QuestionService(db)
    if not await service.delete(question_id):
        raise HTTPException(status_code=404, detail="Question not found")
    await db.commit()
