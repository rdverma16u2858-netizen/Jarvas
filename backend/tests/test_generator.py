"""
Tests for practice question generation.
═══════════════════════════════════════════════════════════════════════════

WHAT MATTERS HERE
    · A question whose answer key SymPy contradicts must never reach a
      student. `test_a_wrong_answer_key_is_rejected` is the whole point of
      this phase.
    · A multiple-choice question whose options do not contain its answer is
      unanswerable, and SymPy cannot see that — so the structural check has
      to.
    · Proof questions have nothing computable in them. They must still be
      returned, marked unconfirmed, or asking for proofs yields nothing.
    · Answers must be withheld until the student asks for them.
"""

import pytest
from httpx import AsyncClient

from app.core.config import settings
from app.llm.factory import reset_provider
from app.math.questions import Question, QuestionType
from app.math.schema import ClaimKind, Difficulty, Topic, Verification

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _use_mock_provider(monkeypatch):
    reset_provider()
    monkeypatch.setattr(settings, "LLM_PROVIDER", "mock")
    yield
    reset_provider()


async def generate(client: AsyncClient, **overrides) -> dict:
    body = {"topic": "calculus", "difficulty": "medium", "type": "multiple_choice", "count": 3}
    body.update(overrides)
    response = await client.post("/api/v1/generate", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def a_question(**overrides) -> Question:
    """A well-formed multiple-choice question, for mutating in structural tests."""
    fields = {
        "number": 1,
        "type": QuestionType.MULTIPLE_CHOICE,
        "topic": Topic.CALCULUS,
        "difficulty": Difficulty.MEDIUM,
        "prompt": "Differentiate x^2",
        "options": ["2x", "x", "x^2/2", "2"],
        "answer": "2x",
        "correct_options": [0],
        "verification": Verification(
            kind=ClaimKind.DERIVATIVE, expression="x**2", variable="x", result="2*x"
        ),
    }
    fields.update(overrides)
    return Question(**fields)


# ── the answer key is checked ─────────────────────────────────────────────


async def test_generated_answers_are_verified_by_sympy(client: AsyncClient) -> None:
    """The premise of the phase: a generated answer key is not taken on trust."""
    body = await generate(client)

    assert body["confirmed"] == len(body["questions"])
    assert all(q["verified"] for q in body["questions"])


async def test_a_wrong_answer_key_is_rejected(client: AsyncClient) -> None:
    """A question whose answer SymPy contradicts must never reach a student.

    A wrong worked solution is visibly wrong. A wrong ANSWER KEY is invisible:
    the student solves it correctly, disagrees, and concludes they are wrong.
    """
    body = await generate(client, concepts="produce a wrong answer key", count=3)

    assert body["rejected"] == 1, "the bad key should have been caught"
    assert len(body["questions"]) == 2, "the good questions should survive"
    assert all(q["verified"] for q in body["questions"])


async def test_proof_questions_are_kept_but_not_confirmed(client: AsyncClient) -> None:
    """Proofs have nothing computable. Dropping them would mean asking for
    proof questions returns an empty set, which looks like a broken feature."""
    body = await generate(client, type="proof", count=2)

    assert len(body["questions"]) == 2
    assert body["confirmed"] == 0
    assert all(q["verified"] is False for q in body["questions"])
    assert all(q["verdict_kind"] == "unverifiable" for q in body["questions"])


async def test_surviving_questions_are_renumbered(client: AsyncClient) -> None:
    """A student must never see "1, 2, 4" and wonder what happened to 3."""
    body = await generate(client, concepts="produce a wrong answer key", count=3)

    assert [q["number"] for q in body["questions"]] == [1, 2]


# ── structural checks SymPy cannot make ───────────────────────────────────


async def test_options_must_contain_the_answer_position() -> None:
    """The maths can verify perfectly while the question is unanswerable."""
    broken = a_question(correct_options=[7])

    assert "points outside" in broken.structural_problem()


async def test_multiple_choice_needs_exactly_one_correct_option() -> None:
    assert "exactly one" in a_question(correct_options=[0, 1]).structural_problem()


async def test_a_non_choice_question_must_not_carry_options() -> None:
    """Options on a short-answer question mean the model misread the format,
    and the UI would render a multiple-choice widget for a written answer."""
    confused = a_question(type=QuestionType.SHORT_ANSWER)

    assert "must not carry options" in confused.structural_problem()


async def test_a_well_formed_question_has_no_structural_problem() -> None:
    assert a_question().structural_problem() == ""


async def test_a_malformed_question_is_not_reported_as_verified() -> None:
    """It must not pass just because the mathematics happened to check out."""
    from app.math.generator import Generator

    verdict = await Generator._check(a_question(correct_options=[9]))

    assert verdict.kind.value == "error"
    assert "malformed" in verdict.detail


# ── the answer is withheld ────────────────────────────────────────────────


async def test_answers_are_withheld_from_the_generated_set(client: AsyncClient) -> None:
    """Practice is not practice if the answer arrives with the question."""
    body = await generate(client)

    for question in body["questions"]:
        assert question["answer"] is None
        assert question["correct_options"] is None
        assert question["solution_outline"] is None


async def test_the_question_text_still_arrives(client: AsyncClient) -> None:
    """Withholding the answer must not withhold the question."""
    body = await generate(client)
    first = body["questions"][0]

    assert first["prompt"]
    assert len(first["options"]) == 4
    assert first["hint"]


async def test_revealing_the_answer_is_a_separate_request(client: AsyncClient) -> None:
    body = await generate(client)
    question_id = body["questions"][0]["id"]

    hidden = (await client.get(f"/api/v1/generate/questions/{question_id}")).json()
    shown = (
        await client.get(f"/api/v1/generate/questions/{question_id}?include_answers=true")
    ).json()

    assert hidden["answer"] is None
    assert shown["answer"]
    assert shown["correct_options"] == [0]


# ── the bank ──────────────────────────────────────────────────────────────


async def test_generated_questions_are_saved(client: AsyncClient) -> None:
    await generate(client)

    bank = (await client.get("/api/v1/generate/questions")).json()

    assert len(bank) == 3
    assert all(q["id"] is not None for q in bank)


async def test_save_false_stores_nothing(client: AsyncClient) -> None:
    body = await generate(client, save=False)

    assert all(q["id"] is None for q in body["questions"])
    assert (await client.get("/api/v1/generate/questions")).json() == []


async def test_the_bank_filters_compose(client: AsyncClient) -> None:
    await generate(client, topic="calculus")

    match = await client.get("/api/v1/generate/questions?topic=calculus&verified_only=true")
    miss = await client.get("/api/v1/generate/questions?topic=graph_theory")

    assert match.json()
    assert miss.json() == []


async def test_earlier_prompts_are_fed_back_to_avoid_repeats(
    client: AsyncClient, db_session
) -> None:
    """Without this, every request for "5 medium integrals" returns the same
    five textbook favourites — the model has no memory across calls."""
    from app.services.questions import QuestionService

    await generate(client)

    prompts = await QuestionService(db_session).recent_prompts(topic="calculus")

    assert len(prompts) == 3
    assert all(prompt for prompt in prompts), "empty prompts teach the model nothing"


# ── attempts ──────────────────────────────────────────────────────────────


async def test_recording_an_attempt_counts_it(client: AsyncClient) -> None:
    body = await generate(client)
    question_id = body["questions"][0]["id"]

    first = await client.post(
        f"/api/v1/generate/questions/{question_id}/attempt", json={"correct": False}
    )
    second = await client.post(
        f"/api/v1/generate/questions/{question_id}/attempt", json={"correct": True}
    )

    assert first.json()["attempts"] == 1
    assert first.json()["correct"] == 0
    assert second.json()["attempts"] == 2
    assert second.json()["correct"] == 1


async def test_the_server_grades_a_choice_question(client: AsyncClient) -> None:
    """The client never receives `correct_options`, so it CANNOT mark this.

    When grading was done client-side, a question whose answer the client did
    not have scored as wrong — every multiple-choice attempt was recorded
    incorrect and the progress figures were quietly meaningless.
    """
    body = await generate(client)
    question_id = body["questions"][0]["id"]

    right = await client.post(
        f"/api/v1/generate/questions/{question_id}/attempt", json={"selected": [0]}
    )

    assert right.json()["was_correct"] is True
    assert right.json()["correct"] == 1


async def test_a_wrong_choice_is_graded_wrong(client: AsyncClient) -> None:
    body = await generate(client)
    question_id = body["questions"][0]["id"]

    wrong = await client.post(
        f"/api/v1/generate/questions/{question_id}/attempt", json={"selected": [2]}
    )

    assert wrong.json()["was_correct"] is False
    assert wrong.json()["correct"] == 0
    assert wrong.json()["attempts"] == 1


async def test_a_partial_multiple_correct_answer_is_wrong(client: AsyncClient) -> None:
    """Picking one of two correct options is not a correct answer."""
    from app.db.session import SessionFactory
    from app.models.question import PracticeQuestion

    body = await generate(client, type="multiple_correct", count=1)
    question_id = body["questions"][0]["id"]

    # The mock marks one option correct; widen it so "partial" is meaningful.
    async with SessionFactory() as session:
        row = await session.get(PracticeQuestion, question_id)
        row.question = {**row.question, "correct_options": [0, 1]}
        await session.commit()

    partial = await client.post(
        f"/api/v1/generate/questions/{question_id}/attempt", json={"selected": [0]}
    )
    complete = await client.post(
        f"/api/v1/generate/questions/{question_id}/attempt", json={"selected": [0, 1]}
    )

    assert partial.json()["was_correct"] is False
    assert complete.json()["was_correct"] is True


async def test_an_attempt_needs_a_grade(client: AsyncClient) -> None:
    """Neither field sent means there is nothing to record."""
    body = await generate(client)

    response = await client.post(
        f"/api/v1/generate/questions/{body['questions'][0]['id']}/attempt", json={}
    )

    assert response.status_code == 422


async def test_an_attempt_reveals_the_answer(client: AsyncClient) -> None:
    """Once the student has committed there is nothing left to withhold."""
    body = await generate(client)
    question_id = body["questions"][0]["id"]

    answered = await client.post(
        f"/api/v1/generate/questions/{question_id}/attempt", json={"correct": True}
    )

    assert answered.json()["answer"]


async def test_unattempted_filter_reflects_attempts(client: AsyncClient) -> None:
    body = await generate(client)
    question_id = body["questions"][0]["id"]

    await client.post(
        f"/api/v1/generate/questions/{question_id}/attempt", json={"correct": True}
    )
    remaining = (await client.get("/api/v1/generate/questions?unattempted_only=true")).json()

    assert len(remaining) == 2


async def test_stats_report_accuracy_per_topic(client: AsyncClient) -> None:
    body = await generate(client)
    await client.post(
        f"/api/v1/generate/questions/{body['questions'][0]['id']}/attempt",
        json={"correct": True},
    )

    stats = (await client.get("/api/v1/generate/stats")).json()
    calculus = next(s for s in stats if s["topic"] == "calculus")

    assert calculus["questions"] == 3
    assert calculus["attempts"] == 1
    assert calculus["accuracy"] == 1.0


async def test_untouched_topics_report_no_accuracy(client: AsyncClient) -> None:
    """None, not 0.0 — "0% correct" and "not started" mean different things."""
    await generate(client)

    stats = (await client.get("/api/v1/generate/stats")).json()

    assert next(s for s in stats if s["topic"] == "calculus")["accuracy"] is None


# ── the vocabulary endpoint ───────────────────────────────────────────────


async def test_topics_endpoint_lists_the_full_vocabulary(client: AsyncClient) -> None:
    """Served rather than hardcoded in the frontend, so the two cannot drift."""
    body = (await client.get("/api/v1/generate/topics")).json()

    assert len(body["topics"]) == 17  # 16 named topics plus "other"
    assert len(body["difficulties"]) == 7
    assert len(body["types"]) == 6


async def test_literal_routes_are_not_shadowed_by_the_id_route(client: AsyncClient) -> None:
    """/generate/topics must not be parsed as question id "topics"."""
    for path in ("topics", "stats", "questions"):
        response = await client.get(f"/api/v1/generate/{path}")
        assert response.status_code == 200, path


# ── failure handling ──────────────────────────────────────────────────────


async def test_missing_question_is_a_404_not_a_500(client: AsyncClient) -> None:
    assert (await client.get("/api/v1/generate/questions/999999")).status_code == 404
    assert (await client.delete("/api/v1/generate/questions/999999")).status_code == 404

    attempt = await client.post(
        "/api/v1/generate/questions/999999/attempt", json={"correct": True}
    )
    assert attempt.status_code == 404


async def test_count_is_bounded(client: AsyncClient) -> None:
    """An unbounded count would truncate mid-object and cost the whole call."""
    response = await client.post(
        "/api/v1/generate",
        json={"topic": "calculus", "count": 500},
    )

    assert response.status_code == 422


async def test_an_unknown_topic_is_rejected(client: AsyncClient) -> None:
    response = await client.post("/api/v1/generate", json={"topic": "astrology"})

    assert response.status_code == 422
