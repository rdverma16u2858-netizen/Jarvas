"""
The extractor — turn a photograph into a problem the student can check.
═══════════════════════════════════════════════════════════════════════════

WHAT THIS DELIBERATELY DOES NOT DO
    Solve. The extraction stops at text, and the student confirms it before
    anything is solved. See ocr.py for why: a misread problem is still a
    solvable problem, so the pipeline would return a verified, confident
    answer to a question nobody asked, and no amount of checking downstream
    can notice.

WHY EXTRACTION IS NEVER CACHED
    Two photographs of the same problem are different images and hash
    differently, so a cache would essentially never hit. The one case where it
    WOULD hit — the same file uploaded twice — is the case where the student
    is most likely retrying because the first reading was wrong.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from pydantic import ValidationError

from app.core.logging import get_logger
from app.llm.base import ImagePart, LLMProvider, Message, ModelTier
from app.llm.errors import LLMError
from app.math.ocr import EXTRACTION_SCHEMA, Extraction, Legibility
from app.math.prompts import OCR_SYSTEM

logger = get_logger(__name__)


class ExtractionError(Exception):
    """Nothing could be read from the image at all."""


@dataclass
class ExtractionResult:
    extraction: Extraction
    model: str = ""
    total_ms: float = 0.0

    @property
    def needs_checking(self) -> bool:
        """Whether the student must look before this is used.

        True unless the reading was clean AND nothing was flagged. Erring
        toward "check it" is deliberate: the cost of an unnecessary glance is
        two seconds, and the cost of skipping a necessary one is a confident
        solution to the wrong problem.
        """
        return self.extraction.legibility is not Legibility.CLEAR or bool(
            self.extraction.uncertain
        )

    @property
    def usable(self) -> bool:
        return self.extraction.legibility is not Legibility.UNREADABLE and bool(
            self.extraction.problem.strip()
        )


class Extractor:
    """Reads a mathematics problem out of an image."""

    def __init__(self, provider: LLMProvider) -> None:
        self._llm = provider

    async def extract(
        self,
        image_base64: str,
        mime_type: str,
        *,
        hint: str = "",
        tier: ModelTier = ModelTier.BALANCED,
    ) -> ExtractionResult:
        """Transcribe the problem in `image_base64`.

        `hint` is optional context the student can supply ("this is question
        14b") — it goes in as text alongside the image and helps when a page
        holds several problems.
        """
        started = time.perf_counter()

        instruction = "Transcribe the mathematics in this image."
        if hint.strip():
            instruction += f"\n\nThe student says: {hint.strip()}"

        message = Message(
            role="user",
            content=instruction,
            images=(ImagePart(mime_type=mime_type, data=image_base64),),
        )

        try:
            response = await self._llm.complete(
                [message],
                tier=tier,
                system=OCR_SYSTEM,
                json_schema=EXTRACTION_SCHEMA,
                # See the module docstring: a cache here would almost never
                # hit, and would hit hardest in exactly the wrong case.
                use_cache=False,
            )
        except LLMError as exc:
            raise ExtractionError(f"the model could not be reached: {exc}") from exc

        try:
            extraction = Extraction.model_validate_json(response.text)
        except ValidationError as exc:
            raise ExtractionError(
                f"the model's reply did not match the extraction schema: {exc}"
            ) from exc

        result = ExtractionResult(
            extraction=extraction,
            model=response.model,
            total_ms=round((time.perf_counter() - started) * 1000, 1),
        )

        logger.info(
            "extracted from image: %s, %d uncertain, %.0fms",
            extraction.legibility.value,
            len(extraction.uncertain),
            result.total_ms,
        )
        return result
