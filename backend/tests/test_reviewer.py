"""
Tests for mistake detection.
═══════════════════════════════════════════════════════════════════════════

WHAT MATTERS HERE
    · `test_correct_work_is_never_marked_wrong` is the phase. A model asked to
      find mistakes will find mistakes; SymPy has to be able to overrule it.
    · The override must work in BOTH directions — a reviewer that calls wrong
      work correct is a different failure with the same cause.
    · Nothing may be overridden when the reference answer is itself
      unconfirmed, because that would just be trusting the model twice.
    · A right answer reached through a fatal error is its own verdict, not
      "correct".
"""

import pytest
from httpx import AsyncClient

from app.core.config import settings
from app.llm.factory import reset_provider
from app.math.review import Review, ReviewVerdict, Severity
from app.math.reviewer import ReviewResult, _reconcile
from app.math.schema import ClaimKind, Verification
from app.math.verifier import Verdict, VerdictKind

pytestmark = pytest.mark.asyncio

PROBLEM = "Evaluate the integral of x*e^x dx"
WORKING = "Let u = x, dv = e^x dx\ndu = dx, v = e^x\n= x e^x + e^x + C"


@pytest.fixture(autouse=True)
def _use_mock_provider(monkeypatch):
    reset_provider()
    monkeypatch.setattr(settings, "LLM_PROVIDER", "mock")
    yield
    reset_provider()


async def submit(client: AsyncClient, working: str = WORKING, **extra) -> dict:
    response = await client.post(
        "/api/v1/review",
        json={"problem": PROBLEM, "working": working, "tier": "fast", **extra},
    )
    assert response.status_code == 200, response.text
    return response.json()


def a_result(
    *,
    verdict: ReviewVerdict,
    answer_kind: VerdictKind = VerdictKind.VERIFIED,
    student_kind: VerdictKind = VerdictKind.VERIFIED,
    fatal: bool = False,
    student_claim: ClaimKind = ClaimKind.EXPRESSION_EQUALITY,
) -> ReviewResult:
    """Build a ReviewResult directly, to drive `_reconcile` in isolation.

    Going through the API would mean expressing each case as mock keywords;
    constructing the object states the case being tested outright.
    """
    review = Review(
        student_answer="x*exp(x) + exp(x)",
        mistakes=(
            [
                {
                    "line": 3,
                    "quote": "+ e^x",
                    "type": "sign",
                    "severity": Severity.FATAL if fatal else Severity.MINOR,
                    "what_went_wrong": "sign",
                    "why_it_is_wrong": "integration by parts subtracts",
                }
            ]
        ),
        verdict=verdict,
        summary="",
        correct_answer="x*exp(x) - exp(x)",
        topic="integral_calculus",
        difficulty="jee_main",
        verification=Verification(kind=ClaimKind.NUMERIC),
        student_check=Verification(kind=student_claim),
    )
    return ReviewResult(
        review=review,
        answer_verdict=Verdict(kind=answer_kind),
        student_verdict=Verdict(kind=student_kind),
    )


# ── the failure this phase exists to prevent ──────────────────────────────


async def test_correct_work_is_never_marked_wrong(client: AsyncClient) -> None:
    """The reviewer calls sound work wrong; SymPy proves the student right.

    A model asked to find mistakes will find mistakes. Being told with
    authority that correct reasoning is flawed teaches a student to distrust
    reasoning that was fine, which is the worst thing this system can do.
    """
    body = await submit(client, "actually right: x e^x - e^x + C")

    assert body["student_was_right"] is True
    assert body["verdict"] != "wrong"
    assert body["overridden_from"] == "wrong", "the bad verdict must be visibly corrected"


async def test_wrong_work_is_never_marked_correct(client: AsyncClient) -> None:
    """The same defect pointing the other way — an agreeable reviewer."""
    body = await submit(client, "falsely praised: x e^x + e^x + C")

    assert body["student_was_right"] is False
    assert body["verdict"] == "wrong"
    assert body["overridden_from"] == "correct"


async def test_an_ordinary_wrong_answer_needs_no_override(client: AsyncClient) -> None:
    """Reconciliation must be the exception, not something that fires
    constantly — that would mean the prompt, not the check, is doing the work."""
    body = await submit(client)

    assert body["student_was_right"] is False
    assert body["verdict"] == "wrong"
    assert body["overridden_from"] is None


# ── reconciliation rules, in isolation ────────────────────────────────────


async def test_nothing_is_overridden_without_a_confirmed_reference_answer() -> None:
    """Overriding on an unconfirmed answer is trusting the same model twice."""
    result = a_result(verdict=ReviewVerdict.WRONG, answer_kind=VerdictKind.UNVERIFIABLE)

    _reconcile(result)

    assert result.review.verdict is ReviewVerdict.WRONG
    assert result.overridden_from is None


async def test_a_right_answer_from_flawed_working_is_its_own_verdict() -> None:
    """Two sign errors that cancel produce a correct answer from broken work.

    Marking that plainly "correct" hides a problem that costs the student next
    time; marking it "wrong" is false.
    """
    result = a_result(verdict=ReviewVerdict.WRONG, fatal=True)

    _reconcile(result)

    assert result.review.verdict is ReviewVerdict.RIGHT_ANSWER_FLAWED_WORKING


async def test_a_right_answer_with_no_fatal_errors_becomes_correct() -> None:
    result = a_result(verdict=ReviewVerdict.WRONG, fatal=False)

    _reconcile(result)

    assert result.review.verdict is ReviewVerdict.CORRECT


async def test_a_correct_verdict_with_fatal_errors_is_downgraded() -> None:
    """The reviewer said correct and also found a fatal error. Both cannot
    stand; the answer is right, the working is not."""
    result = a_result(verdict=ReviewVerdict.CORRECT, fatal=True)

    _reconcile(result)

    assert result.review.verdict is ReviewVerdict.RIGHT_ANSWER_FLAWED_WORKING
    assert result.overridden_from is ReviewVerdict.CORRECT


async def test_a_clean_correct_verdict_is_left_alone() -> None:
    result = a_result(verdict=ReviewVerdict.CORRECT, fatal=False)

    _reconcile(result)

    assert result.review.verdict is ReviewVerdict.CORRECT
    assert result.overridden_from is None


async def test_an_unanswered_attempt_is_not_reconciled() -> None:
    """`student_check` of kind none means there was no answer to compare."""
    result = a_result(
        verdict=ReviewVerdict.INCOMPLETE,
        student_claim=ClaimKind.NONE,
        student_kind=VerdictKind.UNVERIFIABLE,
    )

    _reconcile(result)

    assert result.review.verdict is ReviewVerdict.INCOMPLETE
    assert result.overridden_from is None


async def test_student_was_right_is_null_when_undetermined() -> None:
    """null and False are different states and must not render alike."""
    result = a_result(verdict=ReviewVerdict.WRONG, answer_kind=VerdictKind.ERROR)

    assert result.student_was_right is None


# ── the response ──────────────────────────────────────────────────────────


async def test_the_review_reports_whether_it_was_itself_checked(
    client: AsyncClient,
) -> None:
    """A review built on an unconfirmed reference answer is one model's
    opinion, and must say so."""
    body = await submit(client)

    assert body["verified"] is True
    assert body["answer_check"]["kind"] == "verified"


async def test_mistakes_carry_what_a_student_needs_to_fix_them(
    client: AsyncClient,
) -> None:
    body = await submit(client)
    mistake = body["review"]["mistakes"][0]

    assert mistake["type"] == "sign"
    assert mistake["severity"] == "fatal"
    assert mistake["quote"], "the student must be able to find the line"
    assert mistake["why_it_is_wrong"], "naming the rule is what teaches"
    assert mistake["correction"]


async def test_corrected_working_resumes_rather_than_restarting(
    client: AsyncClient,
) -> None:
    """Their correct work up to the mistake should still count."""
    body = await submit(client)

    assert body["review"]["corrected_working"]
    assert len(body["review"]["corrected_working"]) < 5


async def test_empty_working_is_rejected(client: AsyncClient) -> None:
    response = await client.post("/api/v1/review", json={"problem": PROBLEM, "working": "   "})

    assert response.status_code in (422, 502)


async def test_oversized_working_is_rejected(client: AsyncClient) -> None:
    """A submission this long is a whole problem set pasted at once."""
    response = await client.post(
        "/api/v1/review", json={"problem": PROBLEM, "working": "x" * 9000}
    )

    assert response.status_code == 422


# ── history and patterns ──────────────────────────────────────────────────


async def test_reviews_are_saved(client: AsyncClient) -> None:
    body = await submit(client)

    assert body["review_id"] is not None
    history = (await client.get("/api/v1/review/history")).json()
    assert len(history) == 1
    assert history[0]["error_types"] == ["sign"]


async def test_save_false_records_nothing(client: AsyncClient) -> None:
    body = await submit(client, save=False)

    assert body["review_id"] is None
    assert (await client.get("/api/v1/review/history")).json() == []


async def test_patterns_rank_the_student_s_recurring_mistakes(
    client: AsyncClient,
) -> None:
    """One review is feedback; several are a pattern they cannot see."""
    for _ in range(3):
        await submit(client)

    patterns = (await client.get("/api/v1/review/patterns")).json()

    assert patterns["reviews"] == 3
    assert patterns["by_error_type"][0] == {"type": "sign", "count": 3}
    assert patterns["most_common_error"] == "sign"


async def test_one_mistake_is_not_yet_a_pattern(client: AsyncClient) -> None:
    """Calling a single slip a pattern would be noise dressed as insight."""
    await submit(client)

    patterns = (await client.get("/api/v1/review/patterns")).json()

    assert patterns["most_common_error"] is None


async def test_override_rate_is_tracked(client: AsyncClient) -> None:
    """A rising rate means the reviewing prompt is drifting."""
    await submit(client)
    await submit(client, "actually right: x e^x - e^x + C")

    health = (await client.get("/api/v1/review/health")).json()

    assert health["reviews"] == 2
    assert health["overridden"] == 1
    assert health["rate"] == 0.5


async def test_literal_routes_are_not_shadowed_by_the_id_route(
    client: AsyncClient,
) -> None:
    """/review/patterns must not be parsed as review id "patterns"."""
    for path in ("patterns", "health", "history"):
        assert (await client.get(f"/api/v1/review/{path}")).status_code == 200, path


async def test_missing_review_is_a_404_not_a_500(client: AsyncClient) -> None:
    assert (await client.get("/api/v1/review/999999")).status_code == 404
    assert (await client.delete("/api/v1/review/999999")).status_code == 404
