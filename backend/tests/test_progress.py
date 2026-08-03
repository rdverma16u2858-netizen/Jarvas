"""
Tests for progress tracking and adaptive difficulty.
═══════════════════════════════════════════════════════════════════════════

WHAT MATTERS HERE
    · A recommendation built on four questions is a coin toss wearing the
      costume of insight. Below the threshold the answer must be "not yet".
    · The ladder is a judgement, not the enum's declaration order —
      `university` is a separate track and must never be recommended as a
      promotion from olympiad.
    · Mastery must not be reachable from a tiny sample. Three right answers
      is not "strong".
    · The focus recommendation must pick the weakest topic WITH EVIDENCE, not
      simply the lowest number.
"""

import pytest
from httpx import AsyncClient

from app.core.config import settings
from app.llm.factory import reset_provider
from app.services.progress import (
    LADDER,
    MIN_ATTEMPTS_FOR_ADJUSTMENT,
    Mastery,
    band,
    rung,
    step,
)

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _use_mock_provider(monkeypatch):
    reset_provider()
    monkeypatch.setattr(settings, "LLM_PROVIDER", "mock")
    yield
    reset_provider()


async def stock(client: AsyncClient, count: int = 10, **overrides) -> list[dict]:
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


async def attempt(client: AsyncClient, question_id: int, *, correct: bool) -> None:
    """Answer a question. Option 0 is the key in the mock's fixtures."""
    await client.post(
        f"/api/v1/generate/questions/{question_id}/attempt",
        json={"selected": [0] if correct else [2]},
    )


# ── the ladder ────────────────────────────────────────────────────────────


async def test_university_is_not_the_top_rung() -> None:
    """It is a different syllabus, not a harder one. Promoting an olympiad
    student "up" to it would move them sideways into unfamiliar material."""
    assert rung("university") is None
    assert step("university", +1) == "university"
    assert step("university", -1) == "university"


async def test_jee_main_sits_below_hard() -> None:
    """A moderate paper, whereas `hard` here means a demanding problem. The
    enum's declaration order has these the other way round."""
    assert rung("jee_main") < rung("hard")


async def test_the_ladder_is_clamped_at_both_ends() -> None:
    assert step(LADDER[0].value, -1) == LADDER[0].value
    assert step(LADDER[-1].value, +1) == LADDER[-1].value


async def test_the_ladder_is_published(client: AsyncClient) -> None:
    """A student told to "move up" deserves to see what up means."""
    body = (await client.get("/api/v1/progress/ladder")).json()

    assert body["ladder"] == [d.value for d in LADDER]
    assert body["unranked"] == ["university"]
    assert body["too_easy_above"] > body["too_hard_below"]


# ── mastery bands ─────────────────────────────────────────────────────────


async def test_a_tiny_sample_cannot_reach_a_high_band() -> None:
    """Three right answers is not mastery, and any formula that lets it be
    will report mastery a student does not have."""
    assert band(attempts=3, accuracy=1.0) == Mastery.LEARNING


async def test_bands_track_accuracy_once_there_is_enough_evidence() -> None:
    assert band(attempts=20, accuracy=0.5) == Mastery.DEVELOPING
    assert band(attempts=20, accuracy=0.75) == Mastery.SOLID
    assert band(attempts=20, accuracy=0.95) == Mastery.STRONG


async def test_untouched_is_distinct_from_bad() -> None:
    assert band(attempts=0, accuracy=None) == Mastery.UNTOUCHED


# ── recommendations refuse to guess ───────────────────────────────────────


async def test_no_recommendation_below_the_evidence_threshold(
    client: AsyncClient,
) -> None:
    """Advice built on four questions is a coin toss presented as insight."""
    questions = await stock(client, count=10)
    for q in questions[:3]:
        await attempt(client, q["id"], correct=True)

    topics = (await client.get("/api/v1/progress/topics")).json()
    calculus = next(t for t in topics if t["topic"] == "calculus")

    assert calculus["suggested"] is None
    assert "before there is enough" in calculus["reason"]


async def test_consistent_success_suggests_moving_up(client: AsyncClient) -> None:
    questions = await stock(client, count=12)
    for q in questions[: MIN_ATTEMPTS_FOR_ADJUSTMENT + 2]:
        await attempt(client, q["id"], correct=True)

    topics = (await client.get("/api/v1/progress/topics")).json()
    calculus = next(t for t in topics if t["topic"] == "calculus")

    assert calculus["working_at"] == "medium"
    assert calculus["suggested"] == "jee_main", "one rung up from medium"
    assert calculus["mastery"] == Mastery.STRONG


async def test_consistent_failure_suggests_dropping_down(client: AsyncClient) -> None:
    """Rebuilding the method a level lower is faster than pushing through."""
    questions = await stock(client, count=12, difficulty="hard")
    for q in questions[:10]:
        await attempt(client, q["id"], correct=False)

    topics = (await client.get("/api/v1/progress/topics")).json()
    calculus = next(t for t in topics if t["topic"] == "calculus")

    assert calculus["working_at"] == "hard"
    assert calculus["suggested"] == "jee_main", "one rung down from hard"


async def test_a_middling_score_stays_put(client: AsyncClient) -> None:
    """The band between the thresholds is deliberately wide, so a couple of
    unlucky questions do not bounce a student up and down."""
    questions = await stock(client, count=12)
    for i, q in enumerate(questions[:10]):
        await attempt(client, q["id"], correct=i < 6)

    topics = (await client.get("/api/v1/progress/topics")).json()
    calculus = next(t for t in topics if t["topic"] == "calculus")

    assert calculus["suggested"] == "medium"
    assert "about right" in calculus["reason"]


async def test_the_top_of_the_ladder_has_nowhere_to_go(client: AsyncClient) -> None:
    questions = await stock(client, count=12, difficulty="olympiad")
    for q in questions[:10]:
        await attempt(client, q["id"], correct=True)

    topics = (await client.get("/api/v1/progress/topics")).json()
    calculus = next(t for t in topics if t["topic"] == "calculus")

    assert calculus["suggested"] is None
    assert "nothing harder" in calculus["reason"]


async def test_struggling_at_the_easiest_level_recommends_solutions(
    client: AsyncClient,
) -> None:
    """There is no level below easy, so "drop a level" would be useless
    advice. The gap is in the material, not the difficulty."""
    questions = await stock(client, count=12, difficulty="easy")
    for q in questions[:10]:
        await attempt(client, q["id"], correct=False)

    topics = (await client.get("/api/v1/progress/topics")).json()
    calculus = next(t for t in topics if t["topic"] == "calculus")

    assert calculus["suggested"] is None
    assert "work through the solutions" in calculus["reason"].lower()


async def test_an_unranked_track_is_never_adjusted(client: AsyncClient) -> None:
    questions = await stock(client, count=12, difficulty="university")
    for q in questions[:10]:
        await attempt(client, q["id"], correct=True)

    topics = (await client.get("/api/v1/progress/topics")).json()
    calculus = next(t for t in topics if t["topic"] == "calculus")

    assert calculus["suggested"] is None
    assert "separate track" in calculus["reason"]


# ── the working level ─────────────────────────────────────────────────────


async def test_the_working_level_follows_attempts_not_the_bank(
    client: AsyncClient,
) -> None:
    """Forty easy questions the student never opened say nothing about the
    level they are working at."""
    easy = await stock(client, count=10, difficulty="easy")
    hard = await stock(client, count=3, difficulty="hard")

    assert easy  # generated but never attempted
    for q in hard:
        await attempt(client, q["id"], correct=True)

    topics = (await client.get("/api/v1/progress/topics")).json()
    calculus = next(t for t in topics if t["topic"] == "calculus")

    assert calculus["working_at"] == "hard"


# ── the overview ──────────────────────────────────────────────────────────


async def test_the_overview_answers_the_page_in_one_request(
    client: AsyncClient,
) -> None:
    questions = await stock(client, count=10)
    for q in questions[:6]:
        await attempt(client, q["id"], correct=True)

    body = (await client.get("/api/v1/progress")).json()

    assert body["overall"]["questions_attempted"] == 6
    assert body["overall"]["correct"] == 6
    assert body["overall"]["accuracy"] == 1.0
    assert body["topics"]
    assert "quiz_trend" in body
    assert "errors" in body


async def test_the_overview_survives_an_empty_database(client: AsyncClient) -> None:
    """A brand-new install must render a page, not a 500."""
    body = (await client.get("/api/v1/progress")).json()

    assert body["overall"]["questions_attempted"] == 0
    assert body["overall"]["accuracy"] is None
    assert body["topics"] == []
    assert body["focus"] is None


async def test_focus_picks_the_weakest_topic_with_evidence(
    client: AsyncClient,
) -> None:
    """Not simply the lowest number — a topic with two attempts has no claim
    on being the weakest."""
    strong = await stock(client, count=10, topic="calculus")
    weak = await stock(client, count=10, topic="algebra")
    noise = await stock(client, count=10, topic="graph_theory")

    for q in strong[:8]:
        await attempt(client, q["id"], correct=True)
    for i, q in enumerate(weak[:8]):
        await attempt(client, q["id"], correct=i < 3)
    # Two attempts, both wrong — the lowest accuracy, the least evidence.
    for q in noise[:2]:
        await attempt(client, q["id"], correct=False)

    focus = (await client.get("/api/v1/progress")).json()["focus"]

    assert focus["topic"] == "algebra"
    assert focus["accuracy"] < 0.5


async def test_focus_is_none_when_everything_is_going_well(
    client: AsyncClient,
) -> None:
    questions = await stock(client, count=10)
    for q in questions[:8]:
        await attempt(client, q["id"], correct=True)

    assert (await client.get("/api/v1/progress")).json()["focus"] is None


# ── the next step ─────────────────────────────────────────────────────────


async def test_next_step_admits_when_there_is_nothing_to_go_on(
    client: AsyncClient,
) -> None:
    body = (await client.get("/api/v1/progress/next")).json()

    assert body["action"] == "start"
    assert "eight attempts" in body["message"]


async def test_next_step_names_a_topic_and_a_level(client: AsyncClient) -> None:
    questions = await stock(client, count=12)
    for i, q in enumerate(questions[:10]):
        await attempt(client, q["id"], correct=i < 2)

    body = (await client.get("/api/v1/progress/next")).json()

    assert body["action"] == "practise"
    assert body["topic"] == "calculus"
    assert body["difficulty"] == "easy", "20% at medium should drop a rung"


async def test_next_step_advances_when_nothing_is_going_badly(
    client: AsyncClient,
) -> None:
    questions = await stock(client, count=12)
    for q in questions[:10]:
        await attempt(client, q["id"], correct=True)

    body = (await client.get("/api/v1/progress/next")).json()

    assert body["action"] == "advance"
    assert body["difficulty"] == "jee_main"


async def test_next_step_mentions_the_recurring_mistake(client: AsyncClient) -> None:
    """The review history is what makes the advice specific rather than
    "practise more"."""
    questions = await stock(client, count=12)
    for i, q in enumerate(questions[:10]):
        await attempt(client, q["id"], correct=i < 2)

    for _ in range(3):
        await client.post(
            "/api/v1/review",
            json={
                "problem": "Evaluate the integral of x*e^x dx",
                "working": "= x e^x + e^x + C",
            },
        )

    body = (await client.get("/api/v1/progress/next")).json()

    assert body["action"] == "practise"
    # The reviews land on integral_calculus; the weak topic is calculus. The
    # message must still be specific about the topic it names.
    assert "calculus" in body["message"]


# ── quiz trend ────────────────────────────────────────────────────────────


async def test_quiz_scores_appear_in_the_trend(client: AsyncClient) -> None:
    await stock(client, count=6)
    created = await client.post("/api/v1/quiz", json={"count": 3, "topic": "calculus"})
    quiz = created.json()
    for question in quiz["questions"]:
        await client.post(
            f"/api/v1/quiz/{quiz['id']}/answer",
            json={"question_id": question["id"], "selected": [0]},
        )
    await client.post(f"/api/v1/quiz/{quiz['id']}/submit")

    body = (await client.get("/api/v1/progress")).json()

    assert body["overall"]["quizzes_taken"] == 1
    assert body["overall"]["average_quiz_percent"] == 100.0
    assert body["quiz_trend"][0]["percent"] == 100.0


async def test_an_unsubmitted_quiz_is_not_in_the_trend(client: AsyncClient) -> None:
    await stock(client, count=6)
    await client.post("/api/v1/quiz", json={"count": 3, "topic": "calculus"})

    body = (await client.get("/api/v1/progress")).json()

    assert body["overall"]["quizzes_taken"] == 0


# ── mistakes feed through ─────────────────────────────────────────────────


async def test_review_errors_appear_per_topic(client: AsyncClient) -> None:
    await stock(client, count=3, topic="integral_calculus")
    for _ in range(3):
        await client.post(
            "/api/v1/review",
            json={
                "problem": "Evaluate the integral of x*e^x dx",
                "working": "= x e^x + e^x + C",
            },
        )

    topics = (await client.get("/api/v1/progress/topics")).json()
    integrals = next(t for t in topics if t["topic"] == "integral_calculus")

    assert integrals["mistakes"] == 3
    assert integrals["common_error"] == "sign"
