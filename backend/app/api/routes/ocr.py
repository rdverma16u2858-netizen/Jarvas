"""
Reading a problem out of a photograph.
═══════════════════════════════════════════════════════════════════════════

    POST /ocr        upload an image, get the transcription back
    GET  /ocr/limits what the endpoint will accept

WHY THIS ENDPOINT DOES NOT SOLVE
    It returns text and stops. The student confirms or corrects the
    transcription, and then sends it to /solve themselves.

    A misread problem is still a well-formed problem. Chain extraction
    straight into solving and the system produces a fluent, SymPy-verified,
    entirely confident answer to a question the student never asked — and
    nothing downstream can detect it, because every check from Phase 2 onward
    validates the answer against the problem it was handed, not against the
    paper the photograph was taken of.

    The only thing that catches a misreading is the student's eye, and they
    only look if the flow makes them.

WHY multipart AND NOT base64 JSON
    A 6 MB photograph becomes an 8 MB JSON string, which has to be built in
    the browser's memory, parsed in the server's, and shows up whole in any
    request log. multipart/form-data streams it as bytes and is what every
    file input already produces.
"""

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel

from app.core.logging import get_logger
from app.core.ratelimit import llm_rate_limit
from app.llm.base import ModelTier
from app.llm.errors import ConfigurationError, RateLimitError
from app.llm.factory import get_provider
from app.math.extractor import ExtractionError, Extractor
from app.math.ocr import (
    ALLOWED_MIME_TYPES,
    MAX_IMAGE_BYTES,
    ImageError,
    validate_image,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/ocr", tags=["ocr"])


class LimitsOut(BaseModel):
    max_bytes: int
    max_megabytes: float
    allowed_types: list[str]


class ExtractionOut(BaseModel):
    # ── the transcription ─────────────────────────────────────────────────
    problem: str
    plain: str
    topic: str

    # ── how much to trust it ──────────────────────────────────────────────
    legibility: str
    #: Specific things the model was unsure of, each naming where and what.
    #: More useful than a confidence number, which a student cannot act on.
    uncertain: list[str]
    #: True unless the reading was clean AND nothing was flagged. The client
    #: uses this to decide how hard to insist the student looks.
    needs_checking: bool
    #: False when nothing usable came out — the client should ask for a better
    #: photograph rather than offering to solve an empty string.
    usable: bool

    # ── the student's own working, if the image showed any ────────────────
    contains_working: bool
    working: str

    notes: str
    model: str = ""
    total_ms: float = 0.0


@router.get("/limits", response_model=LimitsOut, summary="What will be accepted")
async def limits() -> LimitsOut:
    """Published so the client can reject an oversized file before spending a
    minute uploading it."""
    return LimitsOut(
        max_bytes=MAX_IMAGE_BYTES,
        max_megabytes=round(MAX_IMAGE_BYTES / (1024 * 1024), 1),
        allowed_types=sorted(ALLOWED_MIME_TYPES),
    )


@router.post(
    "",
    dependencies=[Depends(llm_rate_limit)],
    response_model=ExtractionOut,
    summary="Read the mathematics in an image",
    responses={
        413: {"description": "The image is too large"},
        415: {"description": "Unsupported image type"},
        429: {"description": "Provider rate limit"},
        502: {"description": "The model could not be reached or gave unusable output"},
        503: {"description": "No LLM provider configured"},
    },
)
async def extract(
    image: UploadFile = File(description="A photograph or screenshot of one problem"),
    hint: str = Form(
        default="",
        max_length=500,
        description="Optional context, e.g. 'question 14b' when the page holds several",
    ),
    tier: ModelTier = Form(default=ModelTier.BALANCED),
) -> ExtractionOut:
    """Transcribe the problem in an image. Does NOT solve it.

    The response leads with `needs_checking` and `uncertain` because the
    student is the only check on a misreading — nothing downstream can tell a
    correctly-solved wrong problem from a correctly-solved right one.
    """
    try:
        llm = get_provider()
    except ConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc

    data = await image.read()

    try:
        encoded = validate_image(data, image.content_type or "")
    except ImageError as exc:
        message = str(exc)
        # 413 and 415 are distinguished so the client can say "crop it" versus
        # "convert it", which are different actions.
        code = 413 if "limit" in message else status.HTTP_415_UNSUPPORTED_MEDIA_TYPE
        raise HTTPException(status_code=code, detail=message) from exc

    try:
        result = await Extractor(llm).extract(
            encoded, image.content_type or "image/png", hint=hint, tier=tier
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
    except ExtractionError as exc:
        logger.exception("extraction failed")
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    extraction = result.extraction
    return ExtractionOut(
        problem=extraction.problem,
        plain=extraction.plain,
        topic=extraction.topic.value,
        legibility=extraction.legibility.value,
        uncertain=extraction.uncertain,
        needs_checking=result.needs_checking,
        usable=result.usable,
        contains_working=extraction.contains_working,
        working=extraction.working,
        notes=extraction.notes,
        model=result.model,
        total_ms=result.total_ms,
    )
