"""
The API router — one place where every route is registered.
═══════════════════════════════════════════════════════════════════════════

WHY THIS FILE EXISTS
    main.py includes exactly one router: this one. Adding a feature means
    adding two lines here, and main.py never grows.

    It also means the whole API surface is visible in one screen. When Phase 5
    adds question generation and Phase 7 adds quizzes, you read this file to
    see what exists rather than grepping for `@app.get`.

HOW A ROUTE'S FULL PATH IS BUILT
    settings.API_PREFIX  +  router prefix  +  route path
        /api/v1          +     /health     +    /live      =  /api/v1/health/live
"""

from fastapi import APIRouter

from app.api.routes import (
    auth,
    conversations,
    generate,
    health,
    llm,
    ocr,
    progress,
    quiz,
    review,
    solve,
)

# Everything mounts under this. The API_PREFIX is added in main.py, not here,
# so this file has no opinion about versioning.
api_router = APIRouter()

# ── Phase 0 ───────────────────────────────────────────────────────────────
api_router.include_router(health.router)

# ── Access ────────────────────────────────────────────────────────────────
# Reachable without a token, by necessity — you cannot log in through the gate.
api_router.include_router(auth.router)

# ── Phase 1 ───────────────────────────────────────────────────────────────
api_router.include_router(llm.router)

# ── Phase 2 ───────────────────────────────────────────────────────────────
api_router.include_router(solve.router)

# ── Phase 4 ───────────────────────────────────────────────────────────────
api_router.include_router(conversations.router)

# ── Phase 5 ───────────────────────────────────────────────────────────────
api_router.include_router(generate.router)

# ── Phase 6 ───────────────────────────────────────────────────────────────
api_router.include_router(review.router)

# ── Phase 7 ───────────────────────────────────────────────────────────────
api_router.include_router(quiz.router)

# ── Phase 8 ───────────────────────────────────────────────────────────────
api_router.include_router(progress.router)

# ── Phase 9 ───────────────────────────────────────────────────────────────
api_router.include_router(ocr.router)
