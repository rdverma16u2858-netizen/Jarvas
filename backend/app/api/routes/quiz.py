"""
Quizzes and mock tests.
═══════════════════════════════════════════════════════════════════════════

    POST   /quiz                    assemble a paper and start the clock
    GET    /quiz                    past papers
    GET    /quiz/available          how many questions a paper could draw on
    GET    /quiz/stats              scores over time
    GET    /quiz/{id}               resume — questions, answers so far, time left
    POST   /quiz/{id}/answer        record one answer (ungraded)
    POST   /quiz/{id}/submit        mark the paper and return the result
    GET    /quiz/{id}/result        the marked paper, with the answer key
    DELETE /quiz/{id}

WHY THE ANSWER KEY IS ONLY ON /result
    While a paper is open, every question comes back without its answer,
    without `correct_options`, and without `is_correct`. There is no flag to
    relax that — a quiz whose answers can be requested mid-paper is not a
    quiz, and making it a parameter would put the decision in the client.

WHY 409 AND NOT 400 WHEN THE TIME IS UP
    A late answer is not a malformed request; it is a well-formed request
    against a resource whose state has moved on. The client distinguishes them
    to show "time is up" rather than "something went wrong".
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.session import get_db
from app.math.questions import QuestionType
from app.math.schema import Difficulty, Topic
from app.models.question import PracticeQuestion
from app.models.quiz import Quiz, QuizAnswer, QuizMode
from app.services.quizzes import MAX_QUESTIONS, NotEnoughQuestionsError, QuizError, QuizService

logger = get_logger(__name__)

router = APIRouter(prefix="/quiz", tags=["quiz"])


# ── request and response shapes ───────────────────────────────────────────


class CreateQuizRequest(BaseModel):
    count: int = Field(default=10, ge=1, le=MAX_QUESTIONS)
    mode: QuizMode = Field(
        default=QuizMode.PRACTICE,
        description="practice = untimed, +1 per correct answer. "
        "mock_test = timed, JEE marking (+4 / -1).",
    )
    topic: Topic | None = None
    difficulty: Difficulty | None = None
    type: QuestionType | None = Field(
        default=None, description="Restrict to one question format"
    )
    time_limit_seconds: int | None = Field(
        default=None,
        ge=0,
        le=6 * 60 * 60,
        description="Omit for the mode's default; 0 for untimed",
    )
    title: str = Field(default="", max_length=200)


class AnswerRequest(BaseModel):
    question_id: int
    selected: list[int] | None = Field(
        default=None, description="Chosen option indices, for choice questions"
    )
    written: str | None = Field(
        default=None, max_length=4000, description="Free text, for written questions"
    )
    self_marked: bool | None = Field(
        default=None,
        description="The student's own mark for a written answer, given at review time",
    )


class QuizQuestionOut(BaseModel):
    """A question as it appears DURING a paper — no answer, no key."""

    id: int
    position: int
    type: str
    topic: str
    difficulty: str
    prompt: str
    options: list[str]
    time_minutes: int

    # What the student has put so far, so a reload restores the paper.
    selected: list[int]
    written: str
    self_marked: bool | None

    # ── only populated once the paper is marked ───────────────────────────
    answer: str | None = None
    answer_latex: str | None = None
    correct_options: list[int] | None = None
    solution_outline: list[str] | None = None
    is_correct: bool | None = None
    marks: float | None = None

    @classmethod
    def of(
        cls,
        answer: QuizAnswer,
        question: PracticeQuestion | None,
        *,
        graded: bool,
    ) -> "QuizQuestionOut":
        body = (question.question if question else {}) or {}
        return cls(
            id=answer.question_id,
            position=answer.position,
            type=question.type if question else "",
            topic=question.topic if question else "",
            difficulty=question.difficulty if question else "",
            # A question deleted from the bank leaves the paper readable
            # rather than crashing the page it appears on.
            prompt=question.prompt if question else "(this question was deleted)",
            options=body.get("options", []),
            time_minutes=body.get("time_minutes", 3),
            selected=answer.selected or [],
            written=answer.written,
            self_marked=answer.self_marked,
            answer=(question.answer if question else "") if graded else None,
            answer_latex=body.get("answer_latex", "") if graded else None,
            correct_options=body.get("correct_options", []) if graded else None,
            solution_outline=body.get("solution_outline", []) if graded else None,
            is_correct=answer.is_correct if graded else None,
            marks=answer.marks if graded else None,
        )


class QuizOut(BaseModel):
    id: int
    title: str
    mode: str
    status: str
    topic: str
    difficulty: str

    question_count: int
    marks_correct: int
    marks_wrong: int

    # ── the clock ─────────────────────────────────────────────────────────
    time_limit_seconds: int
    #: null when untimed. Computed server-side on every read — the client is
    #: never the authority on how much time is left.
    seconds_remaining: int | None
    elapsed_seconds: int

    # ── the result, zero until submitted ──────────────────────────────────
    score: float
    max_score: float
    percent: float | None
    correct_count: int
    wrong_count: int
    unattempted_count: int
    #: Correct as a fraction of ATTEMPTED — a different question from the score.
    accuracy: float | None

    questions: list[QuizQuestionOut] = Field(default_factory=list)

    @classmethod
    def of(
        cls,
        quiz: Quiz,
        questions: dict[int, PracticeQuestion] | None = None,
        *,
        graded: bool,
        include_questions: bool = True,
    ) -> "QuizOut":
        lookup = questions or {}
        return cls(
            id=quiz.id,
            title=quiz.title,
            mode=quiz.mode,
            status=quiz.status,
            topic=quiz.topic,
            difficulty=quiz.difficulty,
            question_count=len(quiz.question_ids),
            marks_correct=quiz.marks_correct,
            marks_wrong=quiz.marks_wrong,
            time_limit_seconds=quiz.time_limit_seconds,
            seconds_remaining=quiz.seconds_remaining(),
            elapsed_seconds=quiz.elapsed_seconds or quiz.seconds_elapsed(),
            score=quiz.score,
            max_score=quiz.max_score,
            percent=(round(100 * quiz.score / quiz.max_score, 1) if quiz.max_score else None),
            correct_count=quiz.correct_count,
            wrong_count=quiz.wrong_count,
            unattempted_count=quiz.unattempted_count,
            accuracy=quiz.accuracy,
            questions=(
                [
                    QuizQuestionOut.of(a, lookup.get(a.question_id), graded=graded)
                    for a in quiz.answers
                ]
                if include_questions
                else []
            ),
        )


class AvailabilityOut(BaseModel):
    available: int
    max_questions: int


# ── creating ──────────────────────────────────────────────────────────────


@router.post(
    "",
    response_model=QuizOut,
    status_code=status.HTTP_201_CREATED,
    summary="Assemble a quiz and start the clock",
    responses={409: {"description": "Not enough verified questions in the bank"}},
)
async def create_quiz(
    request: CreateQuizRequest, db: AsyncSession = Depends(get_db)
) -> QuizOut:
    """Draw a paper from the question bank.

    Only questions whose answer key SymPy confirmed are eligible. There is no
    option to relax that: a quiz attaches a score to being marked wrong, and
    doing that against an unchecked key is the failure Phase 5 exists to
    prevent.
    """
    service = QuizService(db)
    try:
        quiz = await service.create(
            count=request.count,
            mode=request.mode,
            topic=request.topic.value if request.topic else None,
            difficulty=request.difficulty.value if request.difficulty else None,
            question_type=request.type.value if request.type else None,
            time_limit_seconds=request.time_limit_seconds,
            title=request.title,
        )
    except NotEnoughQuestionsError as exc:
        # 409, not 422: the request was valid, the bank simply cannot fill it.
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    await db.commit()
    return QuizOut.of(quiz, await service.questions_for(quiz), graded=False)


# ── literal paths — MUST precede /{quiz_id} ───────────────────────────────


@router.get("/available", response_model=AvailabilityOut, summary="How many could be drawn")
async def available(
    topic: str | None = None,
    difficulty: str | None = None,
    type: str | None = None,
    db: AsyncSession = Depends(get_db),
) -> AvailabilityOut:
    """So the UI can say "12 available" rather than letting someone ask for 20
    and be refused."""
    count = await QuizService(db).available(
        topic=topic, difficulty=difficulty, question_type=type
    )
    return AvailabilityOut(available=count, max_questions=MAX_QUESTIONS)


@router.get("/stats", summary="Scores over time")
async def stats(db: AsyncSession = Depends(get_db)) -> dict:
    return await QuizService(db).stats()


@router.get("", response_model=list[QuizOut], summary="Past papers")
async def list_quizzes(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> list[QuizOut]:
    """Summaries only — a history list has no use for every question body."""
    quizzes = await QuizService(db).list(limit=limit, offset=offset)
    return [QuizOut.of(q, graded=False, include_questions=False) for q in quizzes]


# ── one paper ─────────────────────────────────────────────────────────────


@router.get(
    "/{quiz_id}",
    response_model=QuizOut,
    summary="Resume a paper",
    responses={404: {"description": "No such quiz"}},
)
async def get_quiz(quiz_id: int, db: AsyncSession = Depends(get_db)) -> QuizOut:
    """Everything needed to redraw the paper, including answers already given.

    The answer key is withheld unless the paper has been submitted — this is
    the endpoint a running quiz polls, and it is where a leak would happen.
    """
    service = QuizService(db)
    quiz = await service.get(quiz_id)
    if quiz is None:
        raise HTTPException(status_code=404, detail="Quiz not found")

    # Expiry is noticed on read, so a paper left open past its deadline is
    # marked expired even if the client never came back to submit it.
    if quiz.is_expired():
        quiz.status = "expired"
        await db.commit()

    graded = quiz.status == "submitted"
    return QuizOut.of(quiz, await service.questions_for(quiz), graded=graded)


@router.post(
    "/{quiz_id}/answer",
    response_model=QuizOut,
    summary="Record an answer, ungraded",
    responses={
        404: {"description": "No such quiz"},
        409: {"description": "Time is up, or the paper is already submitted"},
    },
)
async def answer(
    quiz_id: int, request: AnswerRequest, db: AsyncSession = Depends(get_db)
) -> QuizOut:
    """Store what the student put. Nothing is marked until submit.

    The response carries no grading information at all — not even for the
    question just answered.
    """
    service = QuizService(db)
    try:
        await service.record_answer(
            quiz_id,
            request.question_id,
            selected=request.selected,
            written=request.written,
            self_marked=request.self_marked,
        )
    except QuizError as exc:
        message = str(exc)
        if "not found" in message:
            raise HTTPException(status_code=404, detail=message) from exc
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=message) from exc

    await db.commit()

    quiz = await service.get(quiz_id)
    assert quiz is not None  # record_answer would have raised
    return QuizOut.of(quiz, await service.questions_for(quiz), graded=False)


@router.post(
    "/{quiz_id}/submit",
    response_model=QuizOut,
    summary="Mark the paper",
    responses={404: {"description": "No such quiz"}},
)
async def submit(quiz_id: int, db: AsyncSession = Depends(get_db)) -> QuizOut:
    """Grade everything at once and return the marked paper with the key.

    Idempotent — submitting twice returns the same result rather than marking
    again, so a double-tapped button cannot produce a second score.
    """
    service = QuizService(db)
    try:
        quiz = await service.submit(quiz_id)
    except QuizError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    await db.commit()
    return QuizOut.of(quiz, await service.questions_for(quiz), graded=True)


@router.get(
    "/{quiz_id}/result",
    response_model=QuizOut,
    summary="The marked paper",
    responses={
        404: {"description": "No such quiz"},
        409: {"description": "This paper has not been submitted yet"},
    },
)
async def result(quiz_id: int, db: AsyncSession = Depends(get_db)) -> QuizOut:
    service = QuizService(db)
    quiz = await service.get(quiz_id)
    if quiz is None:
        raise HTTPException(status_code=404, detail="Quiz not found")

    if quiz.status != "submitted":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This paper has not been submitted yet — submit it to see the answers.",
        )

    return QuizOut.of(quiz, await service.questions_for(quiz), graded=True)


@router.delete(
    "/{quiz_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a paper",
    responses={404: {"description": "No such quiz"}},
)
async def delete_quiz(quiz_id: int, db: AsyncSession = Depends(get_db)) -> None:
    if not await QuizService(db).delete(quiz_id):
        raise HTTPException(status_code=404, detail="Quiz not found")
    await db.commit()
