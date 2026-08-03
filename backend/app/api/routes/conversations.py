"""
Conversations, history, search and bookmarks.
═══════════════════════════════════════════════════════════════════════════

    GET    /conversations              list, newest activity first
    POST   /conversations              start an empty thread
    GET    /conversations/{id}         one thread with all its turns
    PATCH  /conversations/{id}         rename or archive
    DELETE /conversations/{id}         delete permanently

    GET    /conversations/search       search problems, answers and notes
    GET    /conversations/bookmarks    saved turns

    POST   /conversations/turns/{id}/bookmark    save / unsave
    PATCH  /conversations/turns/{id}/note        attach a note

WHY /search AND /bookmarks SIT ABOVE /{id}
    FastAPI matches routes in declaration order. Declared after `/{id}`, a
    request for `/conversations/search` would be captured by the dynamic route
    and fail trying to parse "search" as an integer. Ordering is load-bearing
    here, not stylistic.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.session import get_db
from app.models.conversation import Conversation, Turn
from app.services.conversations import ConversationService

logger = get_logger(__name__)

router = APIRouter(prefix="/conversations", tags=["conversations"])


# ── response shapes ───────────────────────────────────────────────────────


class TurnSummary(BaseModel):
    """A turn without its solution body — for lists, where the full
    derivation would be dead weight in every row."""

    id: int
    conversation_id: int
    problem: str
    final_answer: str
    topic: str
    difficulty: str
    verified: bool
    verdict_kind: str
    bookmarked: bool
    note: str
    created_at: str

    @classmethod
    def of(cls, turn: Turn) -> "TurnSummary":
        return cls(
            id=turn.id,
            conversation_id=turn.conversation_id,
            problem=turn.problem,
            final_answer=turn.final_answer,
            topic=turn.topic,
            difficulty=turn.difficulty,
            verified=turn.verified,
            verdict_kind=turn.verdict_kind,
            bookmarked=turn.bookmarked,
            note=turn.note,
            created_at=turn.created_at.isoformat() if turn.created_at else "",
        )


class TurnDetail(TurnSummary):
    """A turn with everything needed to re-render the full solution card."""

    solution: dict
    verdict: dict
    model: str
    tier: str
    latency_ms: float

    @classmethod
    def of(cls, turn: Turn) -> "TurnDetail":
        return cls(
            **TurnSummary.of(turn).model_dump(),
            solution=turn.solution,
            verdict=turn.verdict,
            model=turn.model,
            tier=turn.tier,
            latency_ms=turn.latency_ms,
        )


class ConversationSummary(BaseModel):
    id: int
    title: str
    turn_count: int
    archived: bool
    last_turn_at: str | None
    created_at: str

    @classmethod
    def of(cls, c: Conversation) -> "ConversationSummary":
        return cls(
            id=c.id,
            title=c.title,
            turn_count=c.turn_count,
            archived=c.archived,
            last_turn_at=c.last_turn_at.isoformat() if c.last_turn_at else None,
            created_at=c.created_at.isoformat() if c.created_at else "",
        )


class ConversationDetail(ConversationSummary):
    turns: list[TurnDetail]


class RenameRequest(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    archived: bool | None = None


class BookmarkRequest(BaseModel):
    bookmarked: bool = True


class NoteRequest(BaseModel):
    note: str = Field(default="", max_length=5000)


# ── list & create ─────────────────────────────────────────────────────────


@router.get("", response_model=list[ConversationSummary], summary="List conversations")
async def list_conversations(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    include_archived: bool = False,
    db: AsyncSession = Depends(get_db),
) -> list[ConversationSummary]:
    service = ConversationService(db)
    return [
        ConversationSummary.of(c)
        for c in await service.list(
            limit=limit, offset=offset, include_archived=include_archived
        )
    ]


@router.post(
    "",
    response_model=ConversationSummary,
    status_code=status.HTTP_201_CREATED,
    summary="Start an empty conversation",
)
async def create_conversation(db: AsyncSession = Depends(get_db)) -> ConversationSummary:
    """Rarely needed: solving with no `conversation_id` creates one anyway.

    This exists for a "New chat" button that wants a thread to exist before
    the first question is asked.
    """
    service = ConversationService(db)
    conversation = await service.create()
    await db.commit()
    return ConversationSummary.of(conversation)


# ── search and bookmarks — MUST precede /{conversation_id} ────────────────


@router.get("/search", response_model=list[TurnSummary], summary="Search history")
async def search(
    q: str = Query(min_length=1, max_length=200, description="Search term"),
    limit: int = Query(default=40, ge=1, le=200),
    topic: str | None = None,
    verified_only: bool = False,
    bookmarked_only: bool = False,
    db: AsyncSession = Depends(get_db),
) -> list[TurnSummary]:
    """Substring search over problems, answers and notes.

    Filters compose: `?q=integral&topic=integral_calculus&verified_only=true`
    finds every verified integral whose text mentions "integral".
    """
    service = ConversationService(db)
    return [
        TurnSummary.of(t)
        for t in await service.search(
            q,
            limit=limit,
            topic=topic,
            verified_only=verified_only,
            bookmarked_only=bookmarked_only,
        )
    ]


@router.get("/bookmarks", response_model=list[TurnDetail], summary="Saved turns")
async def bookmarks(
    limit: int = Query(default=100, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> list[TurnDetail]:
    """Full detail, not summaries — the bookmarks page renders whole solution
    cards, so returning summaries would mean a second request per bookmark."""
    service = ConversationService(db)
    return [TurnDetail.of(t) for t in await service.bookmarks(limit=limit)]


# ── one conversation ──────────────────────────────────────────────────────


@router.get(
    "/{conversation_id}",
    response_model=ConversationDetail,
    summary="One conversation with its turns",
    responses={404: {"description": "No such conversation"}},
)
async def get_conversation(
    conversation_id: int, db: AsyncSession = Depends(get_db)
) -> ConversationDetail:
    service = ConversationService(db)
    conversation = await service.get(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    return ConversationDetail(
        **ConversationSummary.of(conversation).model_dump(),
        turns=[TurnDetail.of(t) for t in conversation.turns],
    )


@router.patch(
    "/{conversation_id}",
    response_model=ConversationSummary,
    summary="Rename or archive",
    responses={404: {"description": "No such conversation"}},
)
async def update_conversation(
    conversation_id: int,
    request: RenameRequest,
    db: AsyncSession = Depends(get_db),
) -> ConversationSummary:
    service = ConversationService(db)

    conversation = None
    if request.title is not None:
        conversation = await service.rename(conversation_id, request.title)
    if request.archived is not None:
        conversation = await service.set_archived(conversation_id, request.archived)

    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    await db.commit()
    return ConversationSummary.of(conversation)


@router.delete(
    "/{conversation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a conversation and all its turns",
    responses={404: {"description": "No such conversation"}},
)
async def delete_conversation(
    conversation_id: int, db: AsyncSession = Depends(get_db)
) -> None:
    """Permanent. The UI offers archiving first — this is the deliberate one."""
    service = ConversationService(db)
    if not await service.delete(conversation_id):
        raise HTTPException(status_code=404, detail="Conversation not found")
    await db.commit()


# ── turns ─────────────────────────────────────────────────────────────────


@router.post(
    "/turns/{turn_id}/bookmark",
    response_model=TurnSummary,
    summary="Save or unsave a turn",
    responses={404: {"description": "No such turn"}},
)
async def bookmark_turn(
    turn_id: int,
    request: BookmarkRequest,
    db: AsyncSession = Depends(get_db),
) -> TurnSummary:
    service = ConversationService(db)
    turn = await service.set_bookmark(turn_id, request.bookmarked)
    if turn is None:
        raise HTTPException(status_code=404, detail="Turn not found")
    await db.commit()
    return TurnSummary.of(turn)


@router.patch(
    "/turns/{turn_id}/note",
    response_model=TurnSummary,
    summary="Attach a note to a turn",
    responses={404: {"description": "No such turn"}},
)
async def note_turn(
    turn_id: int,
    request: NoteRequest,
    db: AsyncSession = Depends(get_db),
) -> TurnSummary:
    """A student's own words about a problem — why they got it wrong, what to
    remember. Searchable alongside the problem text."""
    service = ConversationService(db)
    turn = await service.set_note(turn_id, request.note)
    if turn is None:
        raise HTTPException(status_code=404, detail="Turn not found")
    await db.commit()
    return TurnSummary.of(turn)
