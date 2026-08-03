"""
Tests for reading a problem out of an image.
═══════════════════════════════════════════════════════════════════════════

WHAT MATTERS HERE
    · The endpoint must NOT solve. A misread problem is still a solvable
      problem, so chaining extraction into solving would produce a verified,
      confident answer to a question nobody asked — and nothing downstream
      could tell.
    · An uncertain reading must say so specifically, and `needs_checking` must
      err toward asking the student to look.
    · The image must actually reach the provider. A pipeline that silently
      drops it would still return plausible text.
    · Oversized and unsupported files must be refused with different statuses,
      because "crop it" and "convert it" are different actions.
"""

import base64
import io
import struct
import zlib

import pytest
from httpx import AsyncClient

from app.core.config import settings
from app.llm.base import ImagePart, Message
from app.llm.factory import reset_provider
from app.math.ocr import MAX_IMAGE_BYTES, ImageError, validate_image

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _use_mock_provider(monkeypatch):
    reset_provider()
    monkeypatch.setattr(settings, "LLM_PROVIDER", "mock")
    yield
    reset_provider()


def a_png(width: int = 4, height: int = 4) -> bytes:
    """A real, minimal PNG.

    Built rather than committed as a fixture file so the test suite carries no
    binary blobs, and so the size can be varied for the limit tests.
    """

    def chunk(kind: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + kind
            + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
        )

    raw = b"".join(b"\x00" + b"\xff\x00\x00" * width for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


async def upload(client: AsyncClient, *, hint: str = "", **overrides) -> dict:
    files = {"image": ("problem.png", io.BytesIO(a_png()), "image/png")}
    files.update(overrides.pop("files", {}))
    response = await client.post("/api/v1/ocr", files=files, data={"hint": hint, **overrides})
    assert response.status_code == 200, response.text
    return response.json()


# ── the endpoint transcribes and stops ────────────────────────────────────


async def test_the_endpoint_returns_text_and_does_not_solve(
    client: AsyncClient,
) -> None:
    """The rule this phase is built around.

    A misread problem is still well-formed, so solving it would return a
    fluent, SymPy-verified answer to a question the student never asked, and
    every check downstream would pass.
    """
    body = await upload(client)

    assert body["problem"]
    assert "solution" not in body
    assert "final_answer" not in body
    assert "verified" not in body


async def test_a_clear_reading_still_reports_what_it_read(
    client: AsyncClient,
) -> None:
    body = await upload(client)

    assert body["legibility"] == "clear"
    assert body["uncertain"] == []
    assert body["needs_checking"] is False
    assert body["usable"] is True
    assert body["plain"], "a plain-text reading is what the student sees first"


# ── uncertainty is specific and errs toward asking ────────────────────────


async def test_an_uncertain_reading_names_what_it_could_not_read(
    client: AsyncClient,
) -> None:
    """A confidence number tells a student nothing they can act on. Naming the
    character points their eye at the place worth checking."""
    body = await upload(client, hint="some symbols are unclear")

    assert body["legibility"] == "partial"
    assert len(body["uncertain"]) == 2
    assert any("upper limit" in u for u in body["uncertain"])
    assert body["needs_checking"] is True


async def test_an_unreadable_image_is_not_usable(client: AsyncClient) -> None:
    """ "Take a clearer photograph" is a useful answer; an invented problem is
    not."""
    body = await upload(client, hint="unreadable")

    assert body["legibility"] == "unreadable"
    assert body["usable"] is False
    assert body["needs_checking"] is True
    assert body["notes"]


async def test_needs_checking_errs_toward_asking(client: AsyncClient) -> None:
    """The cost of an unnecessary glance is two seconds; the cost of skipping
    a necessary one is a confident solution to the wrong problem."""
    from app.math.extractor import ExtractionResult
    from app.math.ocr import Extraction, Legibility

    clean = ExtractionResult(
        extraction=Extraction(problem="x", legibility=Legibility.CLEAR, uncertain=[])
    )
    flagged = ExtractionResult(
        extraction=Extraction(
            problem="x", legibility=Legibility.CLEAR, uncertain=["the exponent"]
        )
    )

    assert clean.needs_checking is False
    assert flagged.needs_checking is True, "a flag beats a clean legibility rating"


# ── the image actually reaches the provider ───────────────────────────────


async def test_the_image_reaches_the_provider(client: AsyncClient) -> None:
    """A pipeline that silently dropped the image would still return plausible
    text, so this asserts the attachment arrived rather than the output."""
    body = await upload(client)

    assert body["notes"] != "[mock] no image was attached"


async def test_gemini_puts_the_image_before_the_text() -> None:
    """Gemini reads a single image best when it precedes the instruction that
    refers to it."""
    from app.llm.gemini import GeminiProvider

    provider = GeminiProvider(api_key="test-key")
    body = provider._build_body(
        [
            Message(
                role="user",
                content="Transcribe this.",
                images=(ImagePart(mime_type="image/png", data="AAAA"),),
            )
        ],
        system=None,
        max_tokens=None,
    )

    parts = body["contents"][0]["parts"]
    assert "inlineData" in parts[0]
    assert parts[0]["inlineData"]["mimeType"] == "image/png"
    assert parts[1]["text"] == "Transcribe this."


async def test_images_do_not_bloat_the_cache_key() -> None:
    """Base64 of a photograph is hundreds of kilobytes; hashing it whole on
    every call — including calls that skip the cache — would be absurd."""
    from app.llm.mock import MockProvider

    provider = MockProvider()
    huge = ImagePart(mime_type="image/png", data="A" * 500_000)
    other = ImagePart(mime_type="image/png", data="B" * 500_000)

    first = provider._cache_key(
        [Message(role="user", content="read", images=(huge,))], "m", None, None, None
    )
    second = provider._cache_key(
        [Message(role="user", content="read", images=(other,))], "m", None, None, None
    )

    assert first != second, "different images must be different requests"
    assert len(first) < 80


async def test_a_text_only_message_is_unchanged_by_the_image_field() -> None:
    """Every existing call site passes no images; their wire format and cache
    keys must not shift."""
    from app.llm.gemini import GeminiProvider

    provider = GeminiProvider(api_key="test-key")
    body = provider._build_body(
        [Message(role="user", content="solve x^2=4")], system=None, max_tokens=None
    )

    assert body["contents"][0]["parts"] == [{"text": "solve x^2=4"}]


# ── uploads are validated ─────────────────────────────────────────────────


async def test_an_unsupported_type_is_415(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/ocr",
        files={"image": ("notes.pdf", io.BytesIO(b"%PDF-1.4"), "application/pdf")},
    )

    assert response.status_code == 415
    assert "png" in response.json()["detail"]


async def test_an_oversized_image_is_413(client: AsyncClient) -> None:
    """A different status from 415, because "crop it" and "convert it" are
    different actions."""
    oversized = b"\x89PNG\r\n\x1a\n" + b"\x00" * (MAX_IMAGE_BYTES + 1)

    response = await client.post(
        "/api/v1/ocr",
        files={"image": ("huge.png", io.BytesIO(oversized), "image/png")},
    )

    assert response.status_code == 413
    assert "Crop it" in response.json()["detail"]


async def test_an_empty_file_is_rejected() -> None:
    with pytest.raises(ImageError, match="empty"):
        validate_image(b"", "image/png")


async def test_a_valid_image_encodes_to_base64() -> None:
    png = a_png()

    encoded = validate_image(png, "image/png")

    assert base64.b64decode(encoded) == png


async def test_the_limits_are_published(client: AsyncClient) -> None:
    """So the client can refuse an oversized file before spending a minute
    uploading it."""
    body = (await client.get("/api/v1/ocr/limits")).json()

    assert body["max_bytes"] == MAX_IMAGE_BYTES
    assert "image/png" in body["allowed_types"]
    assert "image/heic" not in body["allowed_types"]


# ── working shown in the image ────────────────────────────────────────────


async def test_working_in_the_image_is_separated_from_the_question(
    client: AsyncClient,
) -> None:
    """A photograph of a whole page usually shows both. Putting them in one
    field would send the student's own attempt to /solve as if it were the
    question."""
    body = await upload(client, hint="this photo also shows my working")

    assert body["contains_working"] is True
    assert body["working"]
    assert body["working"] not in body["problem"]


async def test_the_hint_reaches_the_model(client: AsyncClient) -> None:
    """A page holding several problems needs "question 14b" to be usable."""
    plain = await upload(client)
    hinted = await upload(client, hint="unreadable")

    assert plain["legibility"] == "clear"
    assert hinted["legibility"] == "unreadable", "the hint changed the reading"
