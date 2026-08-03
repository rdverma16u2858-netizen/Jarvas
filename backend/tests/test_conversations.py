"""
Tests for conversations, history, search and bookmarks.
═══════════════════════════════════════════════════════════════════════════

WHAT MATTERS HERE
    · A follow-up question must see the earlier turns — that IS the feature.
      `test_context_is_replayed_as_a_conversation` asserts the model receives
      real prior turns, not just the latest question.
    · Deleting a conversation must not orphan its turns.
    · Search must not be fooled by SQL wildcards in the query.
    · A save failure must never destroy the solution the student is waiting
      for.
"""

import pytest
from httpx import AsyncClient

from app.core.config import settings
from app.llm.factory import reset_provider

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _use_mock_provider(monkeypatch):
    """Every test here runs on the mock — no network, no quota."""
    reset_provider()
    monkeypatch.setattr(settings, "LLM_PROVIDER", "mock")
    yield
    reset_provider()


async def solve(client: AsyncClient, problem: str, **extra) -> dict:
    response = await client.post(
        "/api/v1/solve", json={"problem": problem, "tier": "fast", **extra}
    )
    assert response.status_code == 200, response.text
    return response.json()


# ── saving ────────────────────────────────────────────────────────────────


async def test_solving_creates_a_conversation_automatically(client: AsyncClient) -> None:
    """A student typing into an empty page should not have to create a thread."""
    body = await solve(client, "Evaluate the integral of ln(1+x)/(1+x^2) from 0 to 1")

    assert body["conversation_id"] is not None
    assert body["turn_id"] is not None


async def test_the_thread_is_titled_from_the_first_problem(client: AsyncClient) -> None:
    """Naming threads is a chore nobody does, so it must be automatic."""
    body = await solve(client, "Evaluate the integral of ln(1+x)/(1+x^2) from 0 to 1")

    detail = (await client.get(f"/api/v1/conversations/{body['conversation_id']}")).json()

    assert detail["title"].startswith("Evaluate the integral")
    assert detail["turn_count"] == 1


async def test_a_second_problem_joins_the_same_thread(client: AsyncClient) -> None:
    first = await solve(client, "first problem")
    second = await solve(client, "second problem", conversation_id=first["conversation_id"])

    assert second["conversation_id"] == first["conversation_id"]

    detail = (await client.get(f"/api/v1/conversations/{first['conversation_id']}")).json()
    assert detail["turn_count"] == 2
    assert len(detail["turns"]) == 2


async def test_save_false_records_nothing(client: AsyncClient) -> None:
    body = await solve(client, "a throwaway question", save=False)

    assert body["turn_id"] is None
    assert (await client.get("/api/v1/conversations")).json() == []


async def test_a_stored_turn_keeps_everything_needed_to_re_render(
    client: AsyncClient,
) -> None:
    """History must be able to redraw the full solution card without re-solving."""
    body = await solve(client, "Evaluate the integral of ln(1+x)/(1+x^2) from 0 to 1")

    turn = (await client.get(f"/api/v1/conversations/{body['conversation_id']}")).json()[
        "turns"
    ][0]

    assert turn["solution"]["steps"], "steps must survive the round trip"
    assert turn["verdict"]["kind"] == "verified"
    assert turn["verified"] is True
    assert turn["topic"] == "integral_calculus"


# ── conversation memory: the actual feature ───────────────────────────────


async def test_context_is_replayed_as_a_conversation() -> None:
    """A follow-up must arrive as a real exchange, not a quoted transcript.

    Without this, "now do it from 0 to infinity" has no antecedent and the
    model either guesses or asks what you mean.
    """
    from app.math.solver import _with_context

    messages = _with_context(
        "now do it from 0 to infinity",
        [("Evaluate the integral from 0 to 1", "pi*log(2)/8")],
    )

    assert [m.role for m in messages] == ["user", "assistant", "user"]
    assert messages[0].content == "Evaluate the integral from 0 to 1"
    assert messages[1].content == "pi*log(2)/8"
    assert messages[-1].content == "now do it from 0 to infinity"


async def test_context_is_bounded(db_session) -> None:
    """A long thread must not grow the request without limit."""
    from app.services.conversations import CONTEXT_TURNS, ConversationService

    service = ConversationService(db_session)
    conversation = await service.create()

    for i in range(CONTEXT_TURNS + 4):
        await service.add_turn(
            conversation_id=conversation.id,
            problem=f"problem {i}",
            solution={"final_answer": f"answer {i}"},
            verdict={"kind": "verified"},
            verified=True,
        )

    context = await service.context_messages(conversation.id)

    assert len(context) == CONTEXT_TURNS
    # Newest turns, in conversation order (oldest of the window first).
    assert context[-1][0] == f"problem {CONTEXT_TURNS + 3}"


async def test_context_only_carries_problem_and_answer(db_session) -> None:
    """Replaying the full solution JSON would blow the token budget."""
    from app.services.conversations import ConversationService

    service = ConversationService(db_session)
    conversation = await service.create()
    await service.add_turn(
        conversation_id=conversation.id,
        problem="p",
        solution={"final_answer": "a", "steps": [{"action": "huge"} for _ in range(50)]},
        verdict={"kind": "verified"},
        verified=True,
    )

    context = await service.context_messages(conversation.id)

    assert context == [("p", "a")], "steps must not be replayed"


# ── history ───────────────────────────────────────────────────────────────


async def test_conversations_are_listed_newest_activity_first(
    client: AsyncClient,
) -> None:
    first = await solve(client, "older problem")
    second = await solve(client, "newer problem")

    listing = (await client.get("/api/v1/conversations")).json()

    assert [c["id"] for c in listing] == [
        second["conversation_id"],
        first["conversation_id"],
    ]


async def test_renaming_does_not_reorder_the_list(client: AsyncClient) -> None:
    """A rename is not new activity, so it must not jump the thread to the top."""
    older = await solve(client, "older problem")
    await solve(client, "newer problem")

    await client.patch(
        f"/api/v1/conversations/{older['conversation_id']}", json={"title": "Renamed"}
    )
    listing = (await client.get("/api/v1/conversations")).json()

    assert listing[0]["id"] != older["conversation_id"]
    assert listing[-1]["title"] == "Renamed"


async def test_archived_threads_are_hidden_by_default(client: AsyncClient) -> None:
    body = await solve(client, "a problem")
    cid = body["conversation_id"]

    await client.patch(f"/api/v1/conversations/{cid}", json={"archived": True})

    assert (await client.get("/api/v1/conversations")).json() == []
    assert len((await client.get("/api/v1/conversations?include_archived=true")).json()) == 1


async def test_deleting_a_conversation_removes_its_turns(client: AsyncClient) -> None:
    """Orphaned turns would accumulate invisibly forever."""
    body = await solve(client, "a problem")
    cid, tid = body["conversation_id"], body["turn_id"]

    assert (await client.delete(f"/api/v1/conversations/{cid}")).status_code == 204
    assert (await client.get(f"/api/v1/conversations/{cid}")).status_code == 404

    # The turn must be gone too — bookmarking it should now 404.
    response = await client.post(
        f"/api/v1/conversations/turns/{tid}/bookmark", json={"bookmarked": True}
    )
    assert response.status_code == 404


# ── search ────────────────────────────────────────────────────────────────


async def test_search_finds_by_problem_text(client: AsyncClient) -> None:
    await solve(client, "Evaluate the integral of ln(1+x)/(1+x^2) from 0 to 1")
    await solve(client, "Differentiate x squared")

    hits = (await client.get("/api/v1/conversations/search?q=integral")).json()

    assert len(hits) == 1
    assert "integral" in hits[0]["problem"]


async def test_search_is_case_insensitive(client: AsyncClient) -> None:
    await solve(client, "Evaluate the INTEGRAL of something")

    assert (await client.get("/api/v1/conversations/search?q=integral")).json()


async def test_search_escapes_sql_wildcards(client: AsyncClient) -> None:
    """A search for "%" must not match every row.

    Unescaped, the term goes straight into a LIKE pattern and turns into
    "match anything" — a silent, confusing wrong result rather than an error.
    """
    await solve(client, "a problem with no percent sign")

    assert (await client.get("/api/v1/conversations/search?q=%25")).json() == []


async def test_search_filters_compose(client: AsyncClient) -> None:
    await solve(client, "integral problem one")

    verified = await client.get("/api/v1/conversations/search?q=integral&verified_only=true")
    wrong_topic = await client.get(
        "/api/v1/conversations/search?q=integral&topic=graph_theory"
    )

    assert verified.json()
    assert wrong_topic.json() == []


async def test_search_route_is_not_shadowed_by_the_id_route(client: AsyncClient) -> None:
    """/conversations/search must not be parsed as conversation id "search".

    FastAPI matches in declaration order, so this is a real ordering bug that
    would surface as a 422 rather than results.
    """
    response = await client.get("/api/v1/conversations/search?q=anything")

    assert response.status_code == 200


# ── bookmarks and notes ───────────────────────────────────────────────────


async def test_bookmarking_a_turn(client: AsyncClient) -> None:
    body = await solve(client, "a memorable problem")

    marked = await client.post(
        f"/api/v1/conversations/turns/{body['turn_id']}/bookmark",
        json={"bookmarked": True},
    )

    assert marked.json()["bookmarked"] is True
    assert len((await client.get("/api/v1/conversations/bookmarks")).json()) == 1


async def test_unbookmarking_removes_it_from_the_list(client: AsyncClient) -> None:
    body = await solve(client, "a problem")
    turn_id = body["turn_id"]

    await client.post(
        f"/api/v1/conversations/turns/{turn_id}/bookmark", json={"bookmarked": True}
    )
    await client.post(
        f"/api/v1/conversations/turns/{turn_id}/bookmark", json={"bookmarked": False}
    )

    assert (await client.get("/api/v1/conversations/bookmarks")).json() == []


async def test_notes_are_searchable(client: AsyncClient) -> None:
    """A student's own words about a problem are worth finding later."""
    body = await solve(client, "an integral problem")

    await client.patch(
        f"/api/v1/conversations/turns/{body['turn_id']}/note",
        json={"note": "I keep forgetting the Jacobian here"},
    )
    hits = (await client.get("/api/v1/conversations/search?q=Jacobian")).json()

    assert len(hits) == 1
    assert "Jacobian" in hits[0]["note"]


async def test_bookmarks_return_full_detail_not_summaries(client: AsyncClient) -> None:
    """The bookmarks page draws whole solution cards; summaries would force a
    second request per bookmark."""
    body = await solve(client, "Evaluate the integral of ln(1+x)/(1+x^2) from 0 to 1")
    await client.post(
        f"/api/v1/conversations/turns/{body['turn_id']}/bookmark",
        json={"bookmarked": True},
    )

    saved = (await client.get("/api/v1/conversations/bookmarks")).json()[0]

    assert saved["solution"]["steps"]
    assert saved["verdict"]["kind"]


# ── failure handling ──────────────────────────────────────────────────────


async def test_missing_conversation_is_a_404_not_a_500(client: AsyncClient) -> None:
    assert (await client.get("/api/v1/conversations/999999")).status_code == 404
    assert (await client.delete("/api/v1/conversations/999999")).status_code == 404


async def test_bookmarking_a_missing_turn_is_a_404(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/conversations/turns/999999/bookmark", json={"bookmarked": True}
    )
    assert response.status_code == 404
