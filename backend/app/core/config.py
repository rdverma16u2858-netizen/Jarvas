"""
Application configuration — the single source of truth for every setting.
═══════════════════════════════════════════════════════════════════════════

WHY THIS FILE EXISTS
    Nothing else in the codebase reads `os.environ` directly. Every setting is
    declared here once, validated by Pydantic at startup, and imported as
    `settings.THING`. If a required value is missing or malformed, the app
    refuses to boot with a clear error instead of failing on the first request.

HOW VALUES ARE RESOLVED (highest priority first)
    1. Real environment variables (what Docker and CI set)
    2. The `.env` file at the project root (what you use locally)
    3. The defaults written below

WHY THE .env PATH IS ABSOLUTE
    It used to be relative ("../.env"), which resolves against the CURRENT
    WORKING DIRECTORY — so it worked when launched from backend/ and silently
    found nothing when uvicorn was launched from the repo root. The symptom was
    an empty API key with a perfectly correct .env file sitting right there.

    Anchoring to this file's own location makes the app behave identically
    however it is started: from backend/, from the repo root, from a container,
    or from a systemd unit with no meaningful CWD at all.

TO ADD A SETTING
    Add a field here, add it to `.env.example` with a comment, done.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# config.py -> core -> app -> backend -> mathbot/
_PROJECT_ROOT = Path(__file__).resolve().parents[3]

# Checked in order; later files override earlier ones. The project-root .env is
# the normal home, backend/.env is an optional local override, and in Docker
# neither exists because the environment is set directly.
_ENV_FILES = (_PROJECT_ROOT / ".env", _PROJECT_ROOT / "backend" / ".env")


def _anchor_sqlite_path(url: str) -> str:
    """Resolve a relative SQLite path against the project root, not the CWD.

    WHY THIS EXISTS
        `sqlite:///./mathbot.db` is relative to whatever directory the process
        happened to start in. Alembic is normally run from `backend/`, while
        uvicorn is often started from the repo root — so the migration creates
        `backend/mathbot.db` and the app then creates and reads an entirely
        separate, empty `mathbot.db` beside it.

        The symptom is "no such table: conversations" on a project whose
        migrations demonstrably ran, which sends you looking at Alembic
        instead of at the path. Anchoring the path removes the ambiguity: one
        database file, wherever either command is launched from.

    Postgres URLs and absolute SQLite paths are returned untouched.
    """
    prefix, separator, path = url.partition(":///")
    if not separator or not prefix.startswith("sqlite"):
        return url

    # ":memory:" and absolute paths are already unambiguous.
    if path.startswith(":") or Path(path).is_absolute():
        return url

    return f"{prefix}:///{(_PROJECT_ROOT / path).resolve().as_posix()}"


class Settings(BaseSettings):
    """Every knob the backend has. Read once at startup, then immutable."""

    model_config = SettingsConfigDict(
        env_file=_ENV_FILES,
        # utf-8-sig, not utf-8: Notepad on Windows writes a UTF-8 byte-order
        # mark, which would otherwise become part of the FIRST key's name and
        # make exactly one setting mysteriously unreadable.
        env_file_encoding="utf-8-sig",
        case_sensitive=True,
        # Ignore unrelated vars (the frontend's NEXT_PUBLIC_* live in the same file)
        extra="ignore",
    )

    # ── Identity ──────────────────────────────────────────────────────────
    APP_NAME: str = "MathBot API"
    APP_VERSION: str = "0.1.0"

    # `local` enables /docs and verbose errors. `production` locks both down.
    ENV: Literal["local", "staging", "production"] = "local"

    # ── HTTP ──────────────────────────────────────────────────────────────
    # Every route is mounted under this. Bumping to /api/v2 later leaves v1
    # running for old clients instead of breaking them.
    API_PREFIX: str = "/api/v1"

    # Browsers block cross-origin requests unless the server allows them.
    # The Next.js dev server runs on 3000; the API on 8000 — different origins.
    CORS_ORIGINS: list[str] = Field(default=["http://localhost:3000"])

    # ── Access ────────────────────────────────────────────────────────────
    # One shared password protecting a one-person study tool from the open
    # internet. Empty = no auth, which is right on localhost and refused in
    # production (see app/core/auth.py).
    #
    # Setting this is what stands between a public URL and someone else
    # spending your Gemini quota and reading your history.
    AUTH_PASSWORD: str = ""

    # Optional. Leave empty and the signing key is derived from the password,
    # so changing the password invalidates every issued token — usually the
    # behaviour you want. Set it separately only if you need tokens to survive
    # a password change.
    AUTH_SECRET: str = ""

    # ── Rate limiting ─────────────────────────────────────────────────────
    # These protect the free-tier quota from accident far more than from
    # abuse: a re-render loop or a double-tapped Generate can spend a day's
    # allowance in a minute. See app/core/ratelimit.py.
    RATE_LIMIT_ENABLED: bool = True

    # Model-calling endpoints: solve, generate, review, ocr.
    # 12/minute is well above any human pace and well below a runaway loop.
    RATE_LIMIT_LLM_PER_MINUTE: int = 12
    # The one that actually guards the quota. Gemini's free tier is stricter
    # than this on some models, so its own 429 stays the real backstop.
    RATE_LIMIT_LLM_PER_DAY: int = 400
    # Database-only reads and writes. Generous — these cost a local query.
    RATE_LIMIT_STANDARD_PER_MINUTE: int = 240

    # Set ONLY when a reverse proxy sits in front, because X-Forwarded-For is
    # client-supplied and spoofable. Off by default: a limiter that silently
    # does nothing is worse than none, because it is believed.
    TRUST_PROXY_HEADER: bool = False

    # ── Database ──────────────────────────────────────────────────────────
    # SQLite by default so the app runs with zero setup. Point this at Postgres
    # (postgresql+asyncpg://user:pass@host:5432/mathbot) and nothing else
    # changes — SQLAlchemy speaks both, and the models are identical.
    #
    # Must be an ASYNC driver: +aiosqlite or +asyncpg. A sync URL will start
    # fine and then deadlock on the first query, so it is validated below.
    DATABASE_URL: str = "sqlite+aiosqlite:///./mathbot.db"

    # Logs every SQL statement. Useful when a query misbehaves, far too noisy
    # to leave on.
    DATABASE_ECHO: bool = False

    # ── Cache ─────────────────────────────────────────────────────────────
    # Optional on purpose. Empty string = use the in-process fallback cache, so
    # you are not forced to run Redis to work on the app. Set it to
    # redis://localhost:6379/0 (docker compose does) for the real thing.
    REDIS_URL: str = ""

    # ── LLM provider ──────────────────────────────────────────────────────
    # Phase 1 builds the provider abstraction that reads these. Declared now so
    # .env.example is complete and you can drop your key in before then.
    #
    # Gemini is the default because it is the only one of the three with a real
    # free tier — the deciding factor for a personal study tool. The whole point
    # of the provider layer is that this is a config change, not a code change:
    # switch to anthropic or openai later and nothing else moves.
    LLM_PROVIDER: Literal["gemini", "anthropic", "openai", "mock"] = "gemini"

    # Optional override that pins EVERY tier to one model. Leave empty to use
    # the per-tier models below. Useful for A/B testing one model app-wide.
    LLM_MODEL: str = ""

    # ── Model tiers ───────────────────────────────────────────────────────
    # Callers request a TIER, never a model name, so these can change without
    # touching application code.
    #
    # Measured against this project's own free-tier key on a hard integral
    # (all three answered correctly):
    #     flash-lite   2.9s     0 thinking tokens
    #     3.5-flash   12.4s   3,041 thinking tokens
    #     3.6-flash   35.7s   8,204 thinking tokens
    #
    # Pro models are NOT usable: they return 429 with no free-tier quota.
    LLM_MODEL_FAST: str = "gemini-3.1-flash-lite-preview"
    LLM_MODEL_BALANCED: str = "gemini-3.5-flash"
    LLM_MODEL_DEEP: str = "gemini-3.6-flash"

    # ── LLM behaviour ─────────────────────────────────────────────────────
    # Generous: a deep-tier model took 35s on one problem, and thinking-heavy
    # requests legitimately run long. Too low a timeout looks like a hang.
    LLM_TIMEOUT: float = 180.0

    # Retries apply to rate limits and 5xx only, never to a bad request.
    LLM_MAX_RETRIES: int = 3

    # 24h. Mathematics does not change, so a correct solution stays correct —
    # and on a free tier, a cache hit is the difference between an answer and
    # a quota error.
    LLM_CACHE_TTL: int = 86_400

    # NEVER hardcode a key. They live in .env, which .gitignore excludes.
    # Only the key for the selected LLM_PROVIDER needs a value.
    GEMINI_API_KEY: str = ""
    ANTHROPIC_API_KEY: str = ""
    OPENAI_API_KEY: str = ""

    @field_validator("DATABASE_URL")
    @classmethod
    def _must_be_async_driver(cls, v: str) -> str:
        """Normalise the driver, then insist it is an async one.

        FastAPI's async request handlers need an async database driver. A
        synchronous URL blocks the event loop and the app appears to hang with
        no error — one of the least obvious failures in async Python.

        BUT hosting platforms hand out the bare scheme. Render, Heroku,
        Railway and Fly all inject `postgres://` or `postgresql://` with no
        driver, because that is what every other language expects. Rejecting
        those means the app cannot read the database its own platform just
        created for it, and the deploy fails at startup with a validation
        error that reads like a configuration mistake rather than a mismatch
        of conventions.

        So the known-good ones are upgraded in place, and anything genuinely
        unusable still raises.
        """
        # `postgres://` is the legacy spelling Heroku popularised and Render
        # still emits; SQLAlchemy 2 dropped support for it entirely.
        for prefix in ("postgresql+psycopg2://", "postgresql://", "postgres://"):
            if v.startswith(prefix):
                v = "postgresql+asyncpg://" + v[len(prefix) :]
                break

        if v.startswith("sqlite://"):
            v = "sqlite+aiosqlite://" + v[len("sqlite://") :]

        if "+aiosqlite" not in v and "+asyncpg" not in v:
            raise ValueError(
                f"DATABASE_URL must use an async driver, got: {v!r}\n"
                "  SQLite:   sqlite+aiosqlite:///./mathbot.db\n"
                "  Postgres: postgresql+asyncpg://user:pass@localhost:5432/mathbot"
            )
        return _anchor_sqlite_path(v)

    @property
    def is_local(self) -> bool:
        """True when running on a developer machine. Gates /docs and tracebacks."""
        return self.ENV == "local"

    @property
    def database_kind(self) -> str:
        """`sqlite` or `postgresql` — for health output and driver-specific tuning."""
        return self.DATABASE_URL.split("+")[0]


@lru_cache
def get_settings() -> Settings:
    """Return the one Settings instance, building it on first call.

    `@lru_cache` makes this a singleton: the .env file is parsed once per
    process, not once per request. Tests override it via FastAPI's dependency
    system rather than mutating a global.
    """
    return Settings()


# Import this everywhere: `from app.core.config import settings`
settings = get_settings()
