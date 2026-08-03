"""
Conversation storage and retrieval.
═══════════════════════════════════════════════════════════════════════════

WHY A SERVICE LAYER RATHER THAN QUERIES IN THE ROUTES
    Route handlers should translate HTTP to intent and back. Once they also
    contain joins and filters, the same query gets written three slightly
    different ways in three endpoints and they drift.

    Everything that touches these tables goes through here.

ON SEARCH
    This uses `ILIKE '%term%'`, not a full-text index, and that is a
    deliberate trade-off worth being explicit about.

    Real full-text search means two implementations: Postgres `tsvector` with
    a GIN index, and SQLite FTS5 virtual tables. They have different syntax,
    different ranking, and different migration requirements — so the app would
    behave differently depending on which database it ran against, which is
    exactly what Phase 0 set out to avoid.

    A substring scan is portable, needs no index maintenance, and for one
    student's few thousand problems it returns in milliseconds. It will not
    scale to a shared deployment with millions of rows, and at that point the
    right move is a Postgres-only FTS path behind this same function.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.conversation import Conversation, Turn

logger = get_logger(__name__)

#: How many previous turns are replayed to the model as context.
#: Six is roughly three exchanges — enough for "why did you use that
#: substitution?" to make sense, short enough that a long thread does not
#: inflate every request. Each turn contributes only its problem and answer
#: (see Turn.summary), not the whole solution.
CONTEXT_TURNS = 6


def _title_from(problem: str) -> str:
    """Derive a conversation title from its first problem.

    Naming a thread is a chore nobody does, so it is never asked for. The
    first line, trimmed, is almost always a good enough label — and it stays
    editable.
    """
    first_line = problem.strip().splitlines()[0] if problem.strip() else "New chat"
    cleaned = " ".join(first_line.split())
    return (cleaned[:77] + "…") if len(cleaned) > 78 else cleaned or "New chat"


class ConversationService:
    """All reads and writes for conversations and turns."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # ── conversations ─────────────────────────────────────────────────────

    async def create(self, *, title: str = "New chat") -> Conversation:
        conversation = Conversation(title=title)
        self.db.add(conversation)
        await self.db.flush()  # assigns the id without ending the transaction
        return conversation

    async def get(self, conversation_id: int) -> Conversation | None:
        return await self.db.get(Conversation, conversation_id)

    async def list(
        self, *, limit: int = 50, offset: int = 0, include_archived: bool = False
    ) -> list[Conversation]:
        """Newest activity first — the order a sidebar wants.

        Sorted by `last_turn_at` rather than `updated_at`: renaming a thread
        should not jump it to the top, because nothing new was said in it.
        Threads with no turns yet fall back to their creation time.
        """
        query = select(Conversation)
        if not include_archived:
            query = query.where(Conversation.archived.is_(False))

        return list(
            (
                await self.db.execute(
                    query.order_by(
                        func.coalesce(
                            Conversation.last_turn_at, Conversation.created_at
                        ).desc()
                    )
                    .limit(limit)
                    .offset(offset)
                )
            )
            .scalars()
            .all()
        )

    async def rename(self, conversation_id: int, title: str) -> Conversation | None:
        conversation = await self.get(conversation_id)
        if conversation is None:
            return None
        conversation.title = title.strip()[:200] or "New chat"
        await self.db.flush()
        return conversation

    async def set_archived(self, conversation_id: int, archived: bool) -> Conversation | None:
        conversation = await self.get(conversation_id)
        if conversation is None:
            return None
        conversation.archived = archived
        await self.db.flush()
        return conversation

    async def delete(self, conversation_id: int) -> bool:
        """Delete permanently, taking every turn with it.

        Archiving is the reversible option and is what the UI offers by
        default; this exists for when someone genuinely means it.
        """
        conversation = await self.get(conversation_id)
        if conversation is None:
            return False
        await self.db.delete(conversation)
        await self.db.flush()
        return True

    # ── turns ─────────────────────────────────────────────────────────────

    async def add_turn(
        self,
        *,
        conversation_id: int | None,
        problem: str,
        solution: dict,
        verdict: dict,
        verified: bool,
        model: str = "",
        tier: str = "balanced",
        latency_ms: float = 0.0,
    ) -> Turn:
        """Record a solved problem, creating the conversation if needed.

        `conversation_id=None` starts a new thread titled from the problem,
        so the caller never has to create one first — the common path is a
        student typing a question into an empty page.
        """
        if conversation_id is None:
            conversation = await self.create(title=_title_from(problem))
        else:
            conversation = await self.get(conversation_id)
            if conversation is None:
                raise LookupError(f"conversation {conversation_id} does not exist")
            # A thread still on its default name takes its title from the
            # first real problem in it.
            if conversation.turn_count == 0 and conversation.title == "New chat":
                conversation.title = _title_from(problem)

        turn = Turn(
            conversation_id=conversation.id,
            problem=problem,
            final_answer=str(solution.get("final_answer", ""))[:4000],
            topic=str(solution.get("topic", "other")),
            difficulty=str(solution.get("difficulty", "medium")),
            verified=verified,
            verdict_kind=str(verdict.get("kind", "error")),
            solution=solution,
            verdict=verdict,
            model=model,
            tier=tier,
            latency_ms=latency_ms,
        )
        self.db.add(turn)

        conversation.turn_count += 1
        # Set from Python rather than func.now() so the value is readable
        # immediately, without a round trip to refresh the row.
        conversation.last_turn_at = datetime.now(UTC)

        await self.db.flush()
        return turn

    async def get_turn(self, turn_id: int) -> Turn | None:
        return await self.db.get(Turn, turn_id)

    async def context_messages(self, conversation_id: int) -> list[tuple[str, str]]:
        """Return the last few exchanges as (problem, answer) pairs.

        This is what makes a follow-up question work. Without it, "now do the
        same from 0 to infinity" has no antecedent and the model either guesses
        or asks what you mean.
        """
        rows = (
            (
                await self.db.execute(
                    select(Turn)
                    .where(Turn.conversation_id == conversation_id)
                    .order_by(Turn.id.desc())
                    .limit(CONTEXT_TURNS)
                )
            )
            .scalars()
            .all()
        )
        # Query is newest-first for the LIMIT; conversation order is oldest-first.
        return [(t.problem, t.final_answer) for t in reversed(rows)]

    # ── bookmarks ─────────────────────────────────────────────────────────

    async def set_bookmark(self, turn_id: int, bookmarked: bool) -> Turn | None:
        turn = await self.get_turn(turn_id)
        if turn is None:
            return None
        turn.bookmarked = bookmarked
        # Timestamped so the bookmarks page can show most-recently-saved
        # first, which is not the same as most-recently-solved.
        turn.bookmarked_at = datetime.now(UTC) if bookmarked else None
        await self.db.flush()
        return turn

    async def set_note(self, turn_id: int, note: str) -> Turn | None:
        turn = await self.get_turn(turn_id)
        if turn is None:
            return None
        turn.note = note[:5000]
        await self.db.flush()
        return turn

    async def bookmarks(self, *, limit: int = 100) -> list[Turn]:
        return list(
            (
                await self.db.execute(
                    select(Turn)
                    .where(Turn.bookmarked.is_(True))
                    .order_by(Turn.bookmarked_at.desc())
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )

    # ── search ────────────────────────────────────────────────────────────

    async def search(
        self,
        query: str,
        *,
        limit: int = 40,
        topic: str | None = None,
        verified_only: bool = False,
        bookmarked_only: bool = False,
    ) -> list[Turn]:
        """Find turns whose problem or answer contains `query`.

        See the module docstring for why this is a substring scan rather than
        a full-text index.
        """
        term = query.strip()
        if not term:
            return []

        # Escape the LIKE wildcards, or a search for "50%" matches everything.
        escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped}%"

        statement = select(Turn).where(
            or_(
                Turn.problem.ilike(pattern, escape="\\"),
                Turn.final_answer.ilike(pattern, escape="\\"),
                Turn.note.ilike(pattern, escape="\\"),
            )
        )

        if topic:
            statement = statement.where(Turn.topic == topic)
        if verified_only:
            statement = statement.where(Turn.verified.is_(True))
        if bookmarked_only:
            statement = statement.where(Turn.bookmarked.is_(True))

        return list(
            (await self.db.execute(statement.order_by(Turn.id.desc()).limit(limit)))
            .scalars()
            .all()
        )
