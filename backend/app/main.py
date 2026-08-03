"""
Application entrypoint — builds and configures the FastAPI app.
═══════════════════════════════════════════════════════════════════════════

WHAT HAPPENS WHEN THE SERVER STARTS
    1. Logging is configured (before anything else, so startup itself logs)
    2. `create_app()` builds the FastAPI instance
    3. The lifespan hook opens the cache connection
    4. Middleware and routes are attached
    5. uvicorn begins accepting requests

RUN IT
    cd backend
    uvicorn app.main:app --reload          # local, auto-restarts on save
    uvicorn app.main:app --host 0.0.0.0    # container
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.router import api_router
from app.cache.client import cache
from app.core import auth
from app.core.config import settings
from app.core.logging import configure_logging, get_logger
from app.db.session import dispose_engine
from app.llm.errors import LLMError
from app.llm.factory import get_provider

# Configure logging at import, before any module logs anything.
configure_logging()
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Startup and shutdown, in one function.

    Everything before `yield` runs once at startup; everything after runs at
    shutdown. This replaces the deprecated @app.on_event handlers and has one
    real advantage: resources are opened and closed in the same scope, so it
    is obvious when a startup step has no matching cleanup.

    The database engine is NOT opened here — SQLAlchemy pools lazily, so it
    connects on first use. It IS disposed on shutdown, because pooled
    connections stay open on the database side otherwise.
    """
    logger.info(
        "starting %s v%s [env=%s, db=%s]",
        settings.APP_NAME,
        settings.APP_VERSION,
        settings.ENV,
        settings.database_kind,
    )

    # Before anything else. In production with no password set this raises and
    # the app does not start — the accident it prevents (deploying publicly
    # with the door open) is silent, and a server that refuses to boot is not.
    auth.require_configured()
    logger.info(
        "access: %s",
        "password required"
        if auth.enabled()
        else "OPEN — no AUTH_PASSWORD set (fine on localhost, never in public)",
    )

    cache.init()

    # Build the LLM provider now so a missing key or an unknown provider name
    # fails at boot with a clear message, rather than on the first question a
    # student asks. A failure here is logged, not fatal: the health endpoints
    # must stay reachable so /llm/status can explain what is wrong.
    try:
        provider = get_provider()
        logger.info("llm ready: %s", provider.name)
    except LLMError as exc:
        logger.error("llm provider unavailable: %s", exc)

    yield  # ── application serves requests here ──

    logger.info("shutting down")
    await cache.close()
    await dispose_engine()


def create_app() -> FastAPI:
    """Build the FastAPI application.

    A factory rather than a module-level `app = FastAPI()` so tests can build
    an isolated instance with overridden settings instead of importing a
    pre-configured global.
    """
    app = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        description=(
            "Backend for an AI mathematics tutor. Solves advanced problems, "
            "verifies every answer with SymPy, and explains each step."
        ),
        lifespan=lifespan,
        # Interactive docs are a development tool. In production they hand an
        # attacker a map of the API, so they are switched off there.
        docs_url="/docs" if settings.is_local else None,
        redoc_url="/redoc" if settings.is_local else None,
        openapi_url="/openapi.json" if settings.is_local else None,
    )

    # ── CORS ──────────────────────────────────────────────────────────────
    # The browser blocks requests from localhost:3000 (Next.js) to
    # localhost:8000 (this API) unless the API says they are allowed. Without
    # this, every frontend fetch fails with an opaque CORS error.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Access ────────────────────────────────────────────────────────────
    # Everything is closed by default and opened by exception. The reverse —
    # listing what to protect — means every route added later is public until
    # someone remembers, which is precisely how this kind of gate fails.
    #
    # The open paths, and why each one has to be:
    #   /auth/*   you cannot log in through the login gate
    #   /health   uptime monitors and container health checks are unauthenticated
    #   /         the landing response, which reveals only the app name
    open_prefixes = (
        f"{settings.API_PREFIX}/auth",
        f"{settings.API_PREFIX}/health",
    )

    @app.middleware("http")
    async def require_password(request: Request, call_next):
        if not auth.enabled():
            return await call_next(request)

        path = request.url.path

        # CORS preflight carries no Authorization header by design — the
        # browser sends it before the real request to ask whether that request
        # is allowed. Rejecting it here would make every cross-origin call fail
        # with an opaque CORS error rather than a 401 the client can act on.
        if request.method == "OPTIONS":
            return await call_next(request)

        if path == "/" or path.startswith(open_prefixes):
            return await call_next(request)

        try:
            auth.verify_token(auth.token_from_header(request.headers.get("authorization")))
        except auth.AuthError as exc:
            return JSONResponse(
                status_code=401,
                content={"error": "unauthorized", "message": str(exc)},
                # Tells the client this is a sign-in problem rather than a
                # broken request, so it can show the login screen instead of
                # an error.
                headers={"WWW-Authenticate": "Bearer"},
            )

        return await call_next(request)

    # ── Routes ────────────────────────────────────────────────────────────
    app.include_router(api_router, prefix=settings.API_PREFIX)

    # ── Unhandled errors ──────────────────────────────────────────────────
    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        """Return JSON for uncaught exceptions instead of an HTML traceback.

        Two reasons this exists:

        · A frontend calling `response.json()` on an HTML error page gets a
          confusing parse error instead of the actual problem.
        · A traceback in a production response leaks file paths and code
          structure, so the detail is only included locally. The full
          exception is always logged either way.
        """
        logger.exception("unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content={
                "error": "internal_error",
                "message": (
                    str(exc) if settings.is_local else "An unexpected error occurred."
                ),
            },
        )

    @app.get("/", include_in_schema=False)
    async def root() -> dict[str, str]:
        """Friendly landing response so hitting the bare host is not a 404."""
        return {
            "name": settings.APP_NAME,
            "version": settings.APP_VERSION,
            "docs": "/docs" if settings.is_local else "disabled",
            "health": f"{settings.API_PREFIX}/health",
        }

    return app


app = create_app()
