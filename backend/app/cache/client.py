"""
Cache layer — Redis when configured, in-process dict when not.
═══════════════════════════════════════════════════════════════════════════

WHY THIS FILE EXISTS
    Later phases lean on caching hard:

    · An identical maths question asked twice should not cost two LLM calls.
      Claude Opus 5 is billed per token; a cache hit is free and instant.
    · Rate limiting needs a shared counter.
    · Quiz sessions need short-lived state that does not deserve a table.

    But requiring Redis to be running before you can work on the app is a bad
    trade for a solo developer. So `Cache` is an interface with two
    implementations, chosen at startup from REDIS_URL.

THE IMPORTANT CAVEAT
    The in-memory fallback is per-process. Two workers do not share it, and it
    empties on restart. That is fine for local development and wrong for
    production — which is why docker-compose and the deploy config both set
    REDIS_URL. `Cache.backend` reports which one is live so /health can show it.
"""

from __future__ import annotations

import json
import time
from typing import Any, Protocol

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class CacheBackend(Protocol):
    """What both implementations must provide.

    A Protocol (structural typing) rather than an ABC: the Redis client is a
    third-party object we adapt, and this keeps the two implementations
    honest without forcing an inheritance relationship.
    """

    async def get(self, key: str) -> Any | None: ...
    async def set(self, key: str, value: Any, ttl: int | None = None) -> None: ...
    async def delete(self, key: str) -> None: ...
    async def incr(self, key: str, ttl: int) -> int: ...
    async def ping(self) -> bool: ...
    async def close(self) -> None: ...


class InMemoryCache:
    """Dict-backed cache with TTL support. Development default.

    Expiry is lazy — entries are checked on read rather than swept by a
    background task. For a local dev cache that is the right trade: no extra
    task to manage, and the only cost is that expired keys hold memory until
    someone asks for them.
    """

    def __init__(self) -> None:
        # key -> (value, expires_at_epoch_seconds | None)
        self._store: dict[str, tuple[Any, float | None]] = {}

    async def get(self, key: str) -> Any | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        value, expires_at = entry
        if expires_at is not None and time.time() > expires_at:
            self._store.pop(key, None)  # expired — evict on read
            return None
        return value

    async def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        expires_at = time.time() + ttl if ttl else None
        self._store[key] = (value, expires_at)

    async def delete(self, key: str) -> None:
        self._store.pop(key, None)

    async def incr(self, key: str, ttl: int) -> int:
        """Increment a counter, setting its expiry on first write.

        The TTL is applied only when the key is created, so a rate-limit
        window does not slide forward every time it is hit — otherwise a
        client making steady requests would extend its own window forever and
        never reset.

        No lock: this runs on the event loop and contains no await between
        the read and the write, so it cannot be interleaved by another task.
        """
        current = await self.get(key)
        count = int(current or 0) + 1
        if current is None:
            self._store[key] = (count, time.time() + ttl)
        else:
            # Keep the original expiry.
            _, expires_at = self._store[key]
            self._store[key] = (count, expires_at)
        return count

    async def ping(self) -> bool:
        return True  # a dict is always up

    async def close(self) -> None:
        self._store.clear()


class RedisCache:
    """Redis-backed cache. Production default.

    Values are JSON-encoded on the way in and decoded on the way out, so
    callers can store dicts and lists rather than hand-rolling serialisation
    at every call site. Anything not JSON-serialisable is stored as its
    string form — deliberate, because a cache write must never take down the
    request that triggered it.
    """

    def __init__(self, url: str) -> None:
        # Imported here, not at module top, so the app runs without the redis
        # package installed when REDIS_URL is empty.
        from redis.asyncio import Redis

        self._redis = Redis.from_url(
            url,
            encoding="utf-8",
            decode_responses=True,
            socket_connect_timeout=5,
            # Keep serving from cache rather than erroring if Redis is briefly
            # unreachable — the caller sees a miss, not a 500.
            retry_on_timeout=True,
        )

    async def get(self, key: str) -> Any | None:
        raw = await self._redis.get(key)
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return raw  # stored as a plain string

    async def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        try:
            payload = json.dumps(value)
        except (TypeError, ValueError):
            payload = str(value)
        await self._redis.set(key, payload, ex=ttl)

    async def delete(self, key: str) -> None:
        await self._redis.delete(key)

    async def incr(self, key: str, ttl: int) -> int:
        """Atomic increment, with the expiry set only on creation.

        INCR and EXPIRE are pipelined into one round trip. Redis INCR is
        atomic, which is the whole reason the rate limiter counts here rather
        than with get-then-set: two requests arriving together must produce
        two, not one.

        EXPIRE uses NX so it applies only when the key has no TTL yet.
        Refreshing it on every hit would let a steady stream of requests push
        its own window forward indefinitely and never be limited.
        """
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.incr(key)
            pipe.expire(key, ttl, nx=True)
            count, _ = await pipe.execute()
        return int(count)

    async def ping(self) -> bool:
        try:
            return bool(await self._redis.ping())
        except Exception as exc:  # noqa: BLE001 — health check must never raise
            logger.warning("redis ping failed: %s", exc)
            return False

    async def close(self) -> None:
        await self._redis.aclose()


class Cache:
    """The object the rest of the app talks to.

    Holds whichever backend was selected at startup and exposes `backend` so
    /health can report `redis` or `memory` — worth surfacing, because
    "everything is slow and nothing is cached across workers" looks identical
    to a code bug until you can see that Redis never connected.
    """

    def __init__(self) -> None:
        self._backend: CacheBackend | None = None
        self.backend: str = "uninitialised"

    def init(self) -> None:
        """Choose and construct the backend. Called once from the app lifespan."""
        if settings.REDIS_URL:
            try:
                self._backend = RedisCache(settings.REDIS_URL)
                self.backend = "redis"
                logger.info("cache: redis at %s", settings.REDIS_URL)
                return
            except Exception as exc:  # noqa: BLE001
                # A missing redis package or a malformed URL should degrade to
                # the fallback, not stop the app from booting.
                logger.warning("cache: redis unavailable (%s) — using memory", exc)

        self._backend = InMemoryCache()
        self.backend = "memory"
        logger.info("cache: in-memory (set REDIS_URL for a shared cache)")

    def _require(self) -> CacheBackend:
        if self._backend is None:
            raise RuntimeError("Cache used before init() — check the app lifespan")
        return self._backend

    async def get(self, key: str) -> Any | None:
        return await self._require().get(key)

    async def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        await self._require().set(key, value, ttl)

    async def delete(self, key: str) -> None:
        await self._require().delete(key)

    async def incr(self, key: str, ttl: int) -> int:
        """Atomically increment a counter that expires after `ttl` seconds."""
        return await self._require().incr(key, ttl)

    async def ping(self) -> bool:
        return await self._require().ping()

    async def close(self) -> None:
        if self._backend is not None:
            await self._backend.close()


# Import this: `from app.cache.client import cache`
cache = Cache()
