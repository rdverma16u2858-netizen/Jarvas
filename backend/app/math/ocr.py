"""
Reading a mathematics problem out of a photograph.
═══════════════════════════════════════════════════════════════════════════

THE RULE THIS PHASE IS BUILT AROUND
    The extracted text is shown to the student and NOT solved automatically.

    Handwriting recognition on mathematics fails in specific, quiet ways: a 7
    read as a 1, a superscript lost so x² becomes x2, a minus sign taken for a
    fraction bar, dx dropped from the end of an integral. Every one of those
    produces a DIFFERENT problem that is still perfectly solvable — so the
    system would return a flawless, verified, confident solution to a question
    the student never asked.

    Verification cannot catch this. SymPy checks that the answer follows from
    the problem it was given; it has no way to know the problem was misread.
    The only thing that can catch it is the student looking at the transcription,
    and they will only do that if they are asked to.

    So this module extracts and stops. Solving is a second, deliberate step.

WHY THE MODEL IS ASKED WHAT IT IS UNSURE ABOUT
    A flat confidence number is nearly useless — "0.82" tells a student
    nothing they can act on. Naming the specific characters it could not read
    ("the exponent on the second term is unclear") points their eye at the
    place worth checking, which is the whole job.
"""

from __future__ import annotations

import base64
import binascii
from enum import Enum

from pydantic import BaseModel, Field

from app.math.schema import Topic, to_gemini_schema

#: Formats worth accepting. HEIC is excluded on purpose: iPhones produce it by
#: default but few models read it, and a silent failure on the format half the
#: photographs arrive in would be worse than an explicit refusal the client can
#: act on by converting.
ALLOWED_MIME_TYPES: frozenset[str] = frozenset(
    {"image/png", "image/jpeg", "image/webp", "image/gif"}
)

#: Largest accepted upload, before base64 expansion. Phone photographs are
#: routinely 3-6 MB; beyond this the request is slow, expensive, and almost
#: certainly a screenshot of a whole page rather than one problem.
MAX_IMAGE_BYTES = 8 * 1024 * 1024


class Legibility(str, Enum):
    """How much of the image could actually be read.

    Deliberately coarse. A percentage invites the client to render a progress
    bar the student cannot act on; these three map onto three different things
    to DO — go ahead, check it first, or take a better photograph.
    """

    CLEAR = "clear"
    PARTIAL = "partial"
    UNREADABLE = "unreadable"


class ImageError(Exception):
    """The upload could not be accepted."""


def validate_image(data: bytes, mime_type: str) -> str:
    """Check an uploaded image and return it base64-encoded.

    Raises ImageError with a message meant for the student, not the log.
    """
    if mime_type not in ALLOWED_MIME_TYPES:
        readable = ", ".join(sorted(m.split("/")[1] for m in ALLOWED_MIME_TYPES))
        raise ImageError(f"{mime_type or 'that file type'} is not supported. Use {readable}.")

    if not data:
        raise ImageError("the file is empty")

    if len(data) > MAX_IMAGE_BYTES:
        megabytes = len(data) / (1024 * 1024)
        raise ImageError(
            f"the image is {megabytes:.1f} MB, over the "
            f"{MAX_IMAGE_BYTES // (1024 * 1024)} MB limit. "
            "Crop it to the single problem you want read."
        )

    try:
        return base64.b64encode(data).decode("ascii")
    except (binascii.Error, ValueError) as exc:  # pragma: no cover - defensive
        raise ImageError("the file could not be read as an image") from exc


# NOTE FOR MAINTAINERS — as elsewhere, these docstrings and Field descriptions
# are prompt text sent to the model. Implementation notes go in comments.


class Extraction(BaseModel):
    """What could be read out of one image."""

    # ── what it says ──────────────────────────────────────────────────────
    problem: str = Field(
        description=(
            "The mathematics problem exactly as written, transcribed into "
            "LaTeX without surrounding $ signs. Transcribe what is ACTUALLY "
            "there — do not correct, simplify or complete it. If the student "
            "wrote something impossible, transcribe the impossible thing."
        )
    )
    plain: str = Field(
        default="",
        description=(
            "The same problem in plain readable text, e.g. 'integrate "
            "x*e^x dx from 0 to 1'. This is what a student sees first."
        ),
    )

    # ── how much to trust it ──────────────────────────────────────────────
    legibility: Legibility = Field(
        description=(
            "clear = every symbol was legible · partial = some symbols were "
            "guessed · unreadable = not enough could be made out to transcribe"
        )
    )
    uncertain: list[str] = Field(
        default_factory=list,
        description=(
            "Specific things you were unsure of, each naming WHERE and WHAT: "
            "'the exponent on the second term could be 2 or z'. Empty if "
            "everything was legible. Do not pad this list."
        ),
    )

    # ── context ───────────────────────────────────────────────────────────
    topic: Topic = Field(default=Topic.OTHER, description="Which topic the problem belongs to")
    contains_working: bool = Field(
        default=False,
        description=(
            "True if the image also shows the student's attempted solution, "
            "not just the question."
        ),
    )
    working: str = Field(
        default="",
        description=(
            "The student's working from the image, one step per line, if any "
            "is shown. Empty otherwise."
        ),
    )
    notes: str = Field(
        default="",
        description=(
            "Anything about the image itself worth telling the student — "
            "'the bottom of the page is cut off', 'the photograph is blurred'. "
            "Empty if there is nothing to say."
        ),
    )


EXTRACTION_SCHEMA = to_gemini_schema(Extraction)
