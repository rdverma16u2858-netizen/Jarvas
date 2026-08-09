"""Short-lived background jobs for practice-question generation.

Render's proxy handles short JSON requests reliably, while an open browser
connection can be closed during a long Gemini-and-SymPy calculation. This
store returns a job ID immediately; the browser then polls a short GET.

Jobs live only in this process because their payload is temporary. Generated
questions are still persisted by the normal question service. If Render
restarts, the browser receives a clear retry message instead of a fake
connection error.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from app.core.logging import get_logger

JOB_TTL_SECONDS = 15 * 60
logger = get_logger(__name__)


@dataclass
class GenerationJob:
    """State for a single accepted generation request."""

    id: str
    idempotency_key: str
    created_at: float = field(default_factory=time.monotonic)
    updated_at: float = field(default_factory=time.monotonic)
    state: str = "queued"
    result: Any | None = None
    error: str | None = None
    error_status: int | None = None
    task: asyncio.Task[None] | None = field(default=None, repr=False)


class GenerationJobs:
    """Coordinate jobs and turn a retried POST into the original job."""

    def __init__(self) -> None:
        self._jobs: dict[str, GenerationJob] = {}
        self._keys: dict[str, str] = {}
        self._lock = asyncio.Lock()

    def _prune(self) -> None:
        cutoff = time.monotonic() - JOB_TTL_SECONDS
        expired = [
            job_id
            for job_id, job in self._jobs.items()
            if job.updated_at < cutoff and job.state in {"completed", "failed"}
        ]
        for job_id in expired:
            job = self._jobs.pop(job_id)
            self._keys.pop(job.idempotency_key, None)

    async def find(self, idempotency_key: str) -> GenerationJob | None:
        """Return the job a previous delivery attempt already made."""
        async with self._lock:
            self._prune()
            job_id = self._keys.get(idempotency_key)
            return self._jobs.get(job_id) if job_id else None

    async def start(
        self,
        idempotency_key: str,
        runner: Callable[[], Awaitable[Any]],
    ) -> GenerationJob:
        """Create one task, or return the task an HTTP retry already started."""
        async with self._lock:
            self._prune()
            existing_id = self._keys.get(idempotency_key)
            if existing_id and (existing := self._jobs.get(existing_id)):
                return existing

            job = GenerationJob(id=str(uuid4()), idempotency_key=idempotency_key)
            self._jobs[job.id] = job
            self._keys[idempotency_key] = job.id
            job.task = asyncio.create_task(
                self._run(job, runner), name=f"generation-job-{job.id}"
            )
            return job

    async def get(self, job_id: str) -> GenerationJob | None:
        """Get a job by its unguessable public ID."""
        async with self._lock:
            self._prune()
            return self._jobs.get(job_id)

    async def _run(self, job: GenerationJob, runner: Callable[[], Awaitable[Any]]) -> None:
        job.state = "running"
        job.updated_at = time.monotonic()
        try:
            job.result = await runner()
            job.state = "completed"
        except asyncio.CancelledError:
            job.state = "failed"
            job.error = "The server restarted while preparing this set. Please generate again."
            job.error_status = 503
            raise
        except Exception as exc:  # noqa: BLE001 - background boundary
            # Route code raises HTTPException with a student-safe detail. Do
            # not expose a raw traceback or provider URL here.
            logger.exception("generation job %s failed", job.id)
            detail = getattr(exc, "detail", None)
            job.error = detail if isinstance(detail, str) else "Could not generate questions."
            job.error_status = int(getattr(exc, "status_code", 502))
            job.state = "failed"
        finally:
            job.updated_at = time.monotonic()

    async def shutdown(self) -> None:
        """Cancel unfinished work before the app releases database resources."""
        async with self._lock:
            tasks = [
                job.task
                for job in self._jobs.values()
                if job.task is not None and not job.task.done()
            ]

        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


generation_jobs = GenerationJobs()
