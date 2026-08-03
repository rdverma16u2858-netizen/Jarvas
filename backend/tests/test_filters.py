"""
Topic and difficulty filtering across the bank, quizzes and progress.
═══════════════════════════════════════════════════════════════════════════

WHY THIS FILE EXISTS
    Until Phase 8 the mock provider ignored the requested topic and difficulty
    and wrote "calculus / medium" onto every generated question. The mock is
    fixed, but the consequence is what this file addresses: every filter in
    Phases 5-8 had only ever been exercised against ONE topic and ONE
    difficulty. They were far less tested than their passing suites implied.

    So these tests use several topics and several difficulties at once, and
    assert that the wrong ones are excluded — not merely that the right ones
    appear. A filter that returns everything passes the second check and fails
    the first.
"""

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


async def generate(client: AsyncClient, **overrides) -> list[dict]:
    body = {
        "topic": "calculus",
        "difficulty": "medium",
        "type": "multiple_choice",
        "count": 3,
    }
    body.update(overrides)
    response = await client.post("/api/v1/generate", json=body)
    assert response.status_code == 200, response.text
    return response.json()["questions"]


async def a_mixed_bank(client: AsyncClient) -> None:
    """Three topics at three difficulties, so a filter has something to get
    wrong."""
    await generate(client, topic="calculus", difficulty="easy", count=2)
    await generate(client, topic="calculus", difficulty="olympiad", count=3)
    await generate(client, topic="algebra", difficulty="medium", count=4)
    await generate(client, topic="graph_theory", difficulty="jee_advanced", count=5)


# ── the generator honours what it was asked for ───────────────────────────


async def test_generated_questions_carry_the_requested_topic_and_difficulty(
    client: AsyncClient,
) -> None:
    """The assumption every filter downstream rests on."""
    questions = await generate(client, topic="number_theory", difficulty="olympiad")

    assert {q["topic"] for q in questions} == {"number_theory"}
    assert {q["difficulty"] for q in questions} == {"olympiad"}


async def test_similar_topic_names_are_not_confused(client: AsyncClient) -> None:
    """ "integral calculus" contains "calculus"; a loose match would collapse
    the two into one topic."""
    questions = await generate(client, topic="integral_calculus")

    assert {q["topic"] for q in questions} == {"integral_calculus"}


async def test_similar_difficulty_names_are_not_confused(
    client: AsyncClient,
) -> None:
    """ "jee advanced" contains "jee ", and must not be read as "jee main"."""
    questions = await generate(client, difficulty="jee_advanced")

    assert {q["difficulty"] for q in questions} == {"jee_advanced"}


# ── the bank ──────────────────────────────────────────────────────────────


async def test_the_bank_filters_by_topic_and_excludes_the_rest(
    client: AsyncClient,
) -> None:
    await a_mixed_bank(client)

    algebra = (await client.get("/api/v1/generate/questions?topic=algebra")).json()

    assert len(algebra) == 4
    assert {q["topic"] for q in algebra} == {"algebra"}


async def test_the_bank_filters_by_difficulty_within_a_topic(
    client: AsyncClient,
) -> None:
    """The case the old mock made untestable: one topic, two difficulties."""
    await a_mixed_bank(client)

    easy = (
        await client.get("/api/v1/generate/questions?topic=calculus&difficulty=easy")
    ).json()
    olympiad = (
        await client.get("/api/v1/generate/questions?topic=calculus&difficulty=olympiad")
    ).json()

    assert len(easy) == 2
    assert len(olympiad) == 3
    assert {q["difficulty"] for q in easy} == {"easy"}
    assert {q["difficulty"] for q in olympiad} == {"olympiad"}


async def test_avoid_repeats_is_scoped_to_the_topic(client: AsyncClient) -> None:
    """Feeding algebra prompts into a graph theory request would waste the
    budget and teach the model nothing useful."""
    from app.services.questions import QuestionService

    await a_mixed_bank(client)

    async with _session() as session:
        algebra = await QuestionService(session).recent_prompts(topic="algebra")
        graphs = await QuestionService(session).recent_prompts(topic="graph_theory")

    assert len(algebra) == 4
    assert len(graphs) == 5


def _session():
    from app.db.session import SessionFactory

    return SessionFactory()


# ── quiz selection ────────────────────────────────────────────────────────


async def test_quiz_availability_respects_both_filters(client: AsyncClient) -> None:
    await a_mixed_bank(client)

    everything = (await client.get("/api/v1/quiz/available")).json()
    calculus = (await client.get("/api/v1/quiz/available?topic=calculus")).json()
    calculus_easy = (
        await client.get("/api/v1/quiz/available?topic=calculus&difficulty=easy")
    ).json()

    assert everything["available"] == 14
    assert calculus["available"] == 5
    assert calculus_easy["available"] == 2


async def test_a_quiz_draws_only_from_the_requested_topic(
    client: AsyncClient,
) -> None:
    await a_mixed_bank(client)

    quiz = (await client.post("/api/v1/quiz", json={"count": 4, "topic": "algebra"})).json()

    assert {q["topic"] for q in quiz["questions"]} == {"algebra"}


async def test_a_quiz_draws_only_from_the_requested_difficulty(
    client: AsyncClient,
) -> None:
    await a_mixed_bank(client)

    quiz = (
        await client.post(
            "/api/v1/quiz",
            json={"count": 3, "topic": "calculus", "difficulty": "olympiad"},
        )
    ).json()

    assert {q["difficulty"] for q in quiz["questions"]} == {"olympiad"}


async def test_a_quiz_refuses_when_the_filtered_pool_is_too_small(
    client: AsyncClient,
) -> None:
    """The bank holds fourteen questions, but only two easy calculus ones.
    Asking for five must fail rather than reaching outside the filter."""
    await a_mixed_bank(client)

    response = await client.post(
        "/api/v1/quiz",
        json={"count": 5, "topic": "calculus", "difficulty": "easy"},
    )

    assert response.status_code == 409
    assert "only 2" in response.json()["detail"]


# ── progress ──────────────────────────────────────────────────────────────


async def test_progress_separates_topics(client: AsyncClient) -> None:
    await a_mixed_bank(client)

    algebra = (await client.get("/api/v1/generate/questions?topic=algebra")).json()
    graphs = (await client.get("/api/v1/generate/questions?topic=graph_theory")).json()
    for q in algebra:
        await client.post(
            f"/api/v1/generate/questions/{q['id']}/attempt", json={"selected": [0]}
        )
    for q in graphs:
        await client.post(
            f"/api/v1/generate/questions/{q['id']}/attempt", json={"selected": [2]}
        )

    topics = {t["topic"]: t for t in (await client.get("/api/v1/progress/topics")).json()}

    assert topics["algebra"]["accuracy"] == 1.0
    assert topics["graph_theory"]["accuracy"] == 0.0
    assert "calculus" in topics, "an untouched topic is still listed"


async def test_the_working_level_is_per_topic(client: AsyncClient) -> None:
    """Two topics practised at different levels must not share one figure."""
    await a_mixed_bank(client)

    olympiad = (
        await client.get("/api/v1/generate/questions?topic=calculus&difficulty=olympiad")
    ).json()
    algebra = (await client.get("/api/v1/generate/questions?topic=algebra")).json()

    for q in olympiad + algebra:
        await client.post(
            f"/api/v1/generate/questions/{q['id']}/attempt", json={"selected": [0]}
        )

    topics = {t["topic"]: t for t in (await client.get("/api/v1/progress/topics")).json()}

    assert topics["calculus"]["working_at"] == "olympiad"
    assert topics["algebra"]["working_at"] == "medium"
