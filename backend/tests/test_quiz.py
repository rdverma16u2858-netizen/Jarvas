"""
Tests for quizzes and mock tests.
═══════════════════════════════════════════════════════════════════════════

WHAT MATTERS HERE
    · The answer key must not be reachable while a paper is open. That is the
      whole difference between a quiz and a worksheet.
    · The clock is the server's. A paper cannot be answered after its
      deadline, and reloading the page must not reset the timer.
    · Only verified questions may be drawn — a score attached to an unchecked
      answer key is the Phase 5 harm with a number on it.
    · Submitting twice must not produce a second, different score.
"""

from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient

from app.core.config import settings
from app.llm.factory import reset_provider

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _use_mock_provider(monkeypatch):
    reset_provider()
    monkeypatch.setattr(settings, "LLM_PROVIDER", "mock")
    yield
    reset_provider()


async def stock_bank(client: AsyncClient, count: int = 6, **overrides) -> list[dict]:
    """Fill the bank, since a quiz can only draw from what Phase 5 produced."""
    body = {
        "topic": "calculus",
        "difficulty": "medium",
        "type": "multiple_choice",
        "count": count,
    }
    body.update(overrides)
    response = await client.post("/api/v1/generate", json=body)
    assert response.status_code == 200, response.text
    return response.json()["questions"]


async def make_quiz(client: AsyncClient, **overrides) -> dict:
    body = {"count": 3, "topic": "calculus"}
    body.update(overrides)
    response = await client.post("/api/v1/quiz", json=body)
    assert response.status_code == 201, response.text
    return response.json()


# ── the answer key is not reachable while the paper is open ───────────────


async def test_a_running_paper_withholds_the_answer_key(client: AsyncClient) -> None:
    """This is the difference between a quiz and a worksheet."""
    await stock_bank(client)
    quiz = await make_quiz(client)

    for question in quiz["questions"]:
        assert question["answer"] is None
        assert question["correct_options"] is None
        assert question["solution_outline"] is None
        assert question["is_correct"] is None


async def test_resuming_a_paper_still_withholds_it(client: AsyncClient) -> None:
    """GET /{id} is what a running quiz polls — the likeliest place to leak."""
    await stock_bank(client)
    quiz = await make_quiz(client)

    reloaded = (await client.get(f"/api/v1/quiz/{quiz['id']}")).json()

    assert all(q["correct_options"] is None for q in reloaded["questions"])


async def test_answering_returns_no_grading_information(client: AsyncClient) -> None:
    """Not even for the question just answered."""
    await stock_bank(client)
    quiz = await make_quiz(client)
    question_id = quiz["questions"][0]["id"]

    response = await client.post(
        f"/api/v1/quiz/{quiz['id']}/answer",
        json={"question_id": question_id, "selected": [0]},
    )

    answered = next(q for q in response.json()["questions"] if q["id"] == question_id)
    assert answered["selected"] == [0], "the answer must be recorded"
    assert answered["is_correct"] is None, "and must not be marked yet"
    assert answered["answer"] is None


async def test_the_result_endpoint_refuses_an_unsubmitted_paper(
    client: AsyncClient,
) -> None:
    """Otherwise /result is a back door to the answers mid-quiz."""
    await stock_bank(client)
    quiz = await make_quiz(client)

    response = await client.get(f"/api/v1/quiz/{quiz['id']}/result")

    assert response.status_code == 409


async def test_submitting_reveals_the_key(client: AsyncClient) -> None:
    await stock_bank(client)
    quiz = await make_quiz(client)

    submitted = (await client.post(f"/api/v1/quiz/{quiz['id']}/submit")).json()

    assert all(q["correct_options"] is not None for q in submitted["questions"])
    assert all(q["answer"] for q in submitted["questions"])


# ── only verified questions ───────────────────────────────────────────────


async def test_a_quiz_only_draws_verified_questions(client: AsyncClient) -> None:
    """A score attached to an unchecked answer key is the Phase 5 harm with a
    number on it. Proof questions are unverifiable, so they are ineligible."""
    await stock_bank(client, count=3, type="proof", topic="real_analysis")

    response = await client.post("/api/v1/quiz", json={"count": 3, "topic": "real_analysis"})

    assert response.status_code == 409, "unverified questions must not be drawn"


async def test_an_empty_bank_is_a_409_with_a_useful_message(
    client: AsyncClient,
) -> None:
    response = await client.post("/api/v1/quiz", json={"count": 5})

    assert response.status_code == 409
    assert "generate more" in response.json()["detail"]


async def test_a_short_bank_does_not_yield_a_short_paper(client: AsyncClient) -> None:
    """A mock test that silently shrinks is not the thing that was asked for."""
    await stock_bank(client, count=2)

    response = await client.post("/api/v1/quiz", json={"count": 10, "topic": "calculus"})

    assert response.status_code == 409
    assert "only 2" in response.json()["detail"]


async def test_availability_is_reported_before_asking(client: AsyncClient) -> None:
    await stock_bank(client, count=4)

    body = (await client.get("/api/v1/quiz/available?topic=calculus")).json()

    assert body["available"] == 4


# ── grading ───────────────────────────────────────────────────────────────


async def test_a_correct_paper_scores_full_marks(client: AsyncClient) -> None:
    await stock_bank(client)
    quiz = await make_quiz(client)

    for question in quiz["questions"]:
        await client.post(
            f"/api/v1/quiz/{quiz['id']}/answer",
            json={"question_id": question["id"], "selected": [0]},
        )
    result = (await client.post(f"/api/v1/quiz/{quiz['id']}/submit")).json()

    assert result["correct_count"] == 3
    assert result["score"] == 3
    assert result["max_score"] == 3
    assert result["percent"] == 100.0


async def test_mock_tests_use_jee_negative_marking(client: AsyncClient) -> None:
    """+4 for correct, -1 for wrong. Practising under it is what teaches a
    student when NOT to attempt — an unmarked quiz cannot."""
    await stock_bank(client)
    quiz = await make_quiz(client, mode="mock_test")

    assert quiz["marks_correct"] == 4
    assert quiz["marks_wrong"] == -1

    ids = [q["id"] for q in quiz["questions"]]
    await client.post(
        f"/api/v1/quiz/{quiz['id']}/answer", json={"question_id": ids[0], "selected": [0]}
    )
    await client.post(
        f"/api/v1/quiz/{quiz['id']}/answer", json={"question_id": ids[1], "selected": [2]}
    )
    result = (await client.post(f"/api/v1/quiz/{quiz['id']}/submit")).json()

    # one right (+4), one wrong (-1), one blank (0)
    assert result["score"] == 3
    assert result["correct_count"] == 1
    assert result["wrong_count"] == 1
    assert result["unattempted_count"] == 1


async def test_a_blank_answer_is_never_penalised(client: AsyncClient) -> None:
    """Under negative marking, leaving a question is a valid strategy and must
    cost nothing."""
    await stock_bank(client)
    quiz = await make_quiz(client, mode="mock_test")

    result = (await client.post(f"/api/v1/quiz/{quiz['id']}/submit")).json()

    assert result["score"] == 0
    assert result["unattempted_count"] == 3


async def test_accuracy_measures_attempted_not_the_whole_paper(
    client: AsyncClient,
) -> None:
    """Two students on 33% — one who attempted everything, one who attempted
    one question and got it right — have opposite problems."""
    await stock_bank(client)
    quiz = await make_quiz(client)

    await client.post(
        f"/api/v1/quiz/{quiz['id']}/answer",
        json={"question_id": quiz["questions"][0]["id"], "selected": [0]},
    )
    result = (await client.post(f"/api/v1/quiz/{quiz['id']}/submit")).json()

    assert result["percent"] == pytest.approx(33.3, abs=0.1)
    assert result["accuracy"] == 1.0


async def test_a_partial_multiple_correct_answer_is_wrong(client: AsyncClient) -> None:
    from app.db.session import SessionFactory
    from app.models.question import PracticeQuestion

    await stock_bank(client, count=3, type="multiple_correct")
    quiz = await make_quiz(client, count=1)
    question_id = quiz["questions"][0]["id"]

    async with SessionFactory() as session:
        row = await session.get(PracticeQuestion, question_id)
        row.question = {**row.question, "correct_options": [0, 1]}
        await session.commit()

    await client.post(
        f"/api/v1/quiz/{quiz['id']}/answer",
        json={"question_id": question_id, "selected": [0]},
    )
    result = (await client.post(f"/api/v1/quiz/{quiz['id']}/submit")).json()

    assert result["correct_count"] == 0


async def test_submitting_twice_does_not_re_mark(client: AsyncClient) -> None:
    """A double-tapped button must not produce a second, different score."""
    await stock_bank(client)
    quiz = await make_quiz(client)
    await client.post(
        f"/api/v1/quiz/{quiz['id']}/answer",
        json={"question_id": quiz["questions"][0]["id"], "selected": [0]},
    )

    first = (await client.post(f"/api/v1/quiz/{quiz['id']}/submit")).json()
    second = (await client.post(f"/api/v1/quiz/{quiz['id']}/submit")).json()

    assert first["score"] == second["score"]
    assert first["correct_count"] == second["correct_count"]


async def test_quiz_results_count_toward_the_question_bank(
    client: AsyncClient,
) -> None:
    """A question answered in a quiz is a question attempted."""
    await stock_bank(client)
    quiz = await make_quiz(client)
    question_id = quiz["questions"][0]["id"]

    await client.post(
        f"/api/v1/quiz/{quiz['id']}/answer",
        json={"question_id": question_id, "selected": [0]},
    )
    await client.post(f"/api/v1/quiz/{quiz['id']}/submit")

    question = (await client.get(f"/api/v1/generate/questions/{question_id}")).json()
    assert question["attempts"] == 1
    assert question["correct"] == 1


# ── the clock ─────────────────────────────────────────────────────────────


async def test_a_mock_test_is_timed_by_default(client: AsyncClient) -> None:
    await stock_bank(client)
    quiz = await make_quiz(client, mode="mock_test")

    assert quiz["time_limit_seconds"] == 3 * 180
    assert quiz["seconds_remaining"] is not None


async def test_a_practice_quiz_is_untimed_by_default(client: AsyncClient) -> None:
    await stock_bank(client)
    quiz = await make_quiz(client)

    assert quiz["time_limit_seconds"] == 0
    assert quiz["seconds_remaining"] is None


async def test_the_clock_is_the_server_s(client: AsyncClient) -> None:
    """Reloading the page must not reset the timer — a tab that sleeps or a
    refresh would otherwise distort it."""
    from app.db.session import SessionFactory
    from app.models.quiz import Quiz

    await stock_bank(client)
    quiz = await make_quiz(client, mode="mock_test", time_limit_seconds=600)

    async with SessionFactory() as session:
        row = await session.get(Quiz, quiz["id"])
        row.started_at = datetime.now(UTC) - timedelta(seconds=100)
        await session.commit()

    reloaded = (await client.get(f"/api/v1/quiz/{quiz['id']}")).json()

    assert 490 <= reloaded["seconds_remaining"] <= 501


async def test_answers_are_refused_after_the_deadline(client: AsyncClient) -> None:
    """Enforced at write time. Checking only at submit would let a whole
    paper be answered late."""
    from app.db.session import SessionFactory
    from app.models.quiz import Quiz

    await stock_bank(client)
    quiz = await make_quiz(client, mode="mock_test", time_limit_seconds=60)

    async with SessionFactory() as session:
        row = await session.get(Quiz, quiz["id"])
        row.started_at = datetime.now(UTC) - timedelta(seconds=120)
        await session.commit()

    response = await client.post(
        f"/api/v1/quiz/{quiz['id']}/answer",
        json={"question_id": quiz["questions"][0]["id"], "selected": [0]},
    )

    assert response.status_code == 409
    assert "time is up" in response.json()["detail"].lower()


async def test_an_expired_paper_can_still_be_submitted(client: AsyncClient) -> None:
    """Whatever was answered before the deadline still counts."""
    from app.db.session import SessionFactory
    from app.models.quiz import Quiz

    await stock_bank(client)
    quiz = await make_quiz(client, mode="mock_test", time_limit_seconds=60)
    await client.post(
        f"/api/v1/quiz/{quiz['id']}/answer",
        json={"question_id": quiz["questions"][0]["id"], "selected": [0]},
    )

    async with SessionFactory() as session:
        row = await session.get(Quiz, quiz["id"])
        row.started_at = datetime.now(UTC) - timedelta(seconds=120)
        await session.commit()

    result = (await client.post(f"/api/v1/quiz/{quiz['id']}/submit")).json()

    assert result["status"] == "submitted"
    assert result["correct_count"] == 1


async def test_remaining_time_never_goes_negative(client: AsyncClient) -> None:
    """A negative number would reach the UI as a count-up, which reads as the
    timer being broken rather than expired."""
    from app.db.session import SessionFactory
    from app.models.quiz import Quiz

    await stock_bank(client)
    quiz = await make_quiz(client, mode="mock_test", time_limit_seconds=60)

    async with SessionFactory() as session:
        row = await session.get(Quiz, quiz["id"])
        row.started_at = datetime.now(UTC) - timedelta(seconds=5000)
        await session.commit()

    reloaded = (await client.get(f"/api/v1/quiz/{quiz['id']}")).json()

    assert reloaded["seconds_remaining"] == 0
    assert reloaded["status"] == "expired"


async def test_a_submitted_paper_refuses_more_answers(client: AsyncClient) -> None:
    await stock_bank(client)
    quiz = await make_quiz(client)
    await client.post(f"/api/v1/quiz/{quiz['id']}/submit")

    response = await client.post(
        f"/api/v1/quiz/{quiz['id']}/answer",
        json={"question_id": quiz["questions"][0]["id"], "selected": [0]},
    )

    assert response.status_code == 409


# ── history ───────────────────────────────────────────────────────────────


async def test_past_papers_are_listed_without_question_bodies(
    client: AsyncClient,
) -> None:
    """A history list has no use for every question body."""
    await stock_bank(client)
    await make_quiz(client)

    listing = (await client.get("/api/v1/quiz")).json()

    assert len(listing) == 1
    assert listing[0]["questions"] == []
    assert listing[0]["question_count"] == 3


async def test_stats_track_scores_over_time(client: AsyncClient) -> None:
    await stock_bank(client)
    quiz = await make_quiz(client)
    for question in quiz["questions"]:
        await client.post(
            f"/api/v1/quiz/{quiz['id']}/answer",
            json={"question_id": question["id"], "selected": [0]},
        )
    await client.post(f"/api/v1/quiz/{quiz['id']}/submit")

    stats = (await client.get("/api/v1/quiz/stats")).json()

    assert stats["quizzes"] == 1
    assert stats["average_percent"] == 100.0
    assert stats["recent"][0]["percent"] == 100.0


async def test_an_unsubmitted_paper_is_not_in_the_stats(client: AsyncClient) -> None:
    """A paper still open has no score to average."""
    await stock_bank(client)
    await make_quiz(client)

    stats = (await client.get("/api/v1/quiz/stats")).json()

    assert stats["quizzes"] == 0
    assert stats["average_percent"] is None


async def test_deleting_a_paper_removes_its_answers(client: AsyncClient) -> None:
    await stock_bank(client)
    quiz = await make_quiz(client)

    assert (await client.delete(f"/api/v1/quiz/{quiz['id']}")).status_code == 204
    assert (await client.get(f"/api/v1/quiz/{quiz['id']}")).status_code == 404


async def test_literal_routes_are_not_shadowed_by_the_id_route(
    client: AsyncClient,
) -> None:
    """/quiz/available must not be parsed as quiz id "available"."""
    for path in ("available", "stats"):
        assert (await client.get(f"/api/v1/quiz/{path}")).status_code == 200, path


async def test_missing_quiz_is_a_404_not_a_500(client: AsyncClient) -> None:
    assert (await client.get("/api/v1/quiz/999999")).status_code == 404
    assert (await client.delete("/api/v1/quiz/999999")).status_code == 404
    assert (await client.post("/api/v1/quiz/999999/submit")).status_code == 404


async def test_answering_a_question_outside_the_paper_is_rejected(
    client: AsyncClient,
) -> None:
    await stock_bank(client)
    quiz = await make_quiz(client, count=1)

    response = await client.post(
        f"/api/v1/quiz/{quiz['id']}/answer",
        json={"question_id": 999999, "selected": [0]},
    )

    assert response.status_code == 409
