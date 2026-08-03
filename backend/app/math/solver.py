"""
The solver — where the model and the verifier meet.
═══════════════════════════════════════════════════════════════════════════

THE LOOP

    ask the model  ->  parse into Solution  ->  verify with SymPy
                                                      |
                            +-------------------------+
                            |
                       REFUTED?  ->  tell the model exactly what SymPy
                                     computed, and ask again (once)
                            |
                       still wrong? -> return it, clearly marked unverified

WHY RETRY ONCE AND NOT MORE
    A model that gets it wrong twice, having been shown the correct value, is
    not going to get it right on the third attempt — it will burn quota and
    make the student wait. One retry catches the genuine slip (an arithmetic
    error, a rounded decimal) which is the common case. Beyond that, the
    honest move is to hand back the answer flagged as unverified.

WHY A REFUTED ANSWER IS STILL RETURNED
    Deleting it would leave the student with nothing. Returning it with the
    verdict attached — "this was checked and the check failed, here is what
    the computer algebra system computed instead" — is more useful and more
    honest than either hiding it or pretending it passed.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from pydantic import ValidationError

from app.core.config import settings
from app.core.logging import get_logger
from app.llm.base import LLMProvider, Message, ModelTier
from app.llm.errors import LLMError
from app.math.prompts import RETRY_SUFFIX, SOLVER_SYSTEM
from app.math.schema import SOLUTION_SCHEMA, Solution
from app.math.verifier import Verdict, VerdictKind, verify

logger = get_logger(__name__)


@dataclass
class Attempt:
    """One pass through the loop. Kept so the retry is visible, not hidden."""

    solution: Solution
    verdict: Verdict
    model: str
    latency_ms: float
    cached: bool


@dataclass
class SolveResult:
    """What the API returns."""

    solution: Solution
    verdict: Verdict
    attempts: list[Attempt] = field(default_factory=list)
    total_ms: float = 0.0

    @property
    def verified(self) -> bool:
        return self.verdict.ok

    @property
    def retried(self) -> bool:
        return len(self.attempts) > 1


class SolverError(Exception):
    """The solver could not produce a solution at all (not: produced a wrong one)."""


class Solver:
    """Solves a problem and verifies the answer before returning it."""

    def __init__(self, provider: LLMProvider) -> None:
        self._llm = provider

    async def solve(
        self,
        problem: str,
        *,
        tier: ModelTier = ModelTier.BALANCED,
        max_attempts: int = 2,
        use_cache: bool = True,
    ) -> SolveResult:
        """Solve `problem`, verify it, and retry once if the check fails.

        Raises SolverError only when no usable solution could be produced —
        the provider failed, or the reply did not fit the schema. A solution
        that was produced and then REFUTED is returned, flagged.
        """
        started = time.perf_counter()
        attempts: list[Attempt] = []
        messages: list[Message] = [Message(role="user", content=problem)]
        system = SOLVER_SYSTEM

        for attempt_no in range(1, max_attempts + 1):
            try:
                response = await self._llm.complete(
                    messages,
                    tier=tier,
                    system=system,
                    json_schema=SOLUTION_SCHEMA,
                    # A retry must NOT be served the cached wrong answer.
                    use_cache=use_cache and attempt_no == 1,
                    cache_ttl=settings.LLM_CACHE_TTL,
                )
            except LLMError as exc:
                if attempts:
                    # An earlier attempt exists; return it rather than nothing.
                    logger.warning("retry failed (%s) — returning first attempt", exc)
                    break
                raise SolverError(f"the model could not be reached: {exc}") from exc

            try:
                solution = Solution.model_validate_json(response.text)
            except ValidationError as exc:
                # Schema-constrained decoding makes this rare, but a truncated
                # response (hit max_tokens mid-object) lands here.
                if attempts:
                    break
                raise SolverError(
                    f"the model's reply did not match the solution schema: {exc}"
                ) from exc

            verdict = await verify(solution.verification)

            attempts.append(
                Attempt(
                    solution=solution,
                    verdict=verdict,
                    model=response.model,
                    latency_ms=response.latency_ms,
                    cached=response.cached,
                )
            )

            logger.info(
                "solve attempt %d/%d  %s  model=%s  %s",
                attempt_no,
                max_attempts,
                verdict.kind.value,
                response.model,
                verdict.detail[:120],
            )

            # Only a REFUTED verdict is worth retrying. VERIFIED is done;
            # UNVERIFIABLE means there was nothing to check, so a second
            # attempt would check nothing again; ERROR means the verifier
            # broke, which the model cannot fix.
            if verdict.kind is not VerdictKind.REFUTED:
                break

            if attempt_no < max_attempts:
                # Feed the computed value back. Being told "you said X, the
                # correct value is Y" is far more effective than "try again".
                messages = [
                    Message(role="user", content=problem),
                    Message(role="assistant", content=response.text),
                    Message(
                        role="user",
                        content=RETRY_SUFFIX.format(
                            claimed=verdict.claimed or "(unclear)",
                            expected=verdict.expected or "(see detail)",
                            detail=verdict.detail,
                        ),
                    ),
                ]

        if not attempts:
            raise SolverError("no solution was produced")

        # Prefer a verified attempt if any exists; otherwise the last one.
        best = next((a for a in attempts if a.verdict.ok), attempts[-1])

        return SolveResult(
            solution=best.solution,
            verdict=best.verdict,
            attempts=attempts,
            total_ms=round((time.perf_counter() - started) * 1000, 1),
        )


# ─────────────────────────────────────────────────────────────────────────
#  Streaming
# ─────────────────────────────────────────────────────────────────────────


@dataclass
class SolveEvent:
    """One event on the stream to the browser.

    `stage` events tell the user what is happening; `delta` carries raw
    generated characters; `result` is the finished, verified solution.
    """

    type: str  # "stage" | "delta" | "result" | "error"
    stage: str = ""
    message: str = ""
    text: str = ""
    payload: dict | None = None


class StreamingSolver(Solver):
    """Solver that reports progress as it works.

    WHY STREAM STAGES AND NOT JUST TOKENS
        The generated text here is JSON, not prose — watching braces and field
        names appear is not useful to a student. What IS useful is knowing
        which stage the request is in, because the two slow parts have very
        different characters:

            solving    5-35s, depends on the model and tier
            verifying  usually under a second

        A wait with a named stage reads as progress. The same wait with a bare
        spinner reads as a hang, and 35 seconds of that is long enough that
        people reload the page.

        `delta` events are still emitted so the UI can show that generation is
        genuinely moving (a character counter, a shimmer) rather than frozen.
    """

    async def solve_stream(
        self,
        problem: str,
        *,
        tier: ModelTier = ModelTier.BALANCED,
    ):
        """Yield SolveEvents through the solve-and-verify pipeline.

        Retries a refuted answer exactly like the non-streaming path. Keeping
        the two in step matters: a student who happened to use the streaming
        UI would otherwise get an uncorrected wrong answer that the plain
        endpoint would have caught and fixed. Same promise, same behaviour.

        The retry is announced as a stage, because it is the one moment the
        wait genuinely gets longer and silence would look like a stall.
        """
        started = time.perf_counter()
        messages: list[Message] = [Message(role="user", content=problem)]
        solution: Solution | None = None
        verdict: Verdict | None = None

        for attempt_no in (1, 2):
            if attempt_no == 1:
                yield SolveEvent(
                    type="stage", stage="solving", message="Working through the problem"
                )
            else:
                yield SolveEvent(
                    type="stage",
                    stage="solving",
                    message="The check failed — working it through again",
                )

            chunks: list[str] = []
            try:
                async for piece in self._llm.stream(
                    messages,
                    tier=tier,
                    system=SOLVER_SYSTEM,
                    json_schema=SOLUTION_SCHEMA,
                ):
                    chunks.append(piece)
                    yield SolveEvent(type="delta", text=piece)
            except LLMError as exc:
                if solution is not None:
                    break  # keep the first attempt rather than losing everything
                yield SolveEvent(type="error", message=str(exc))
                return

            raw = "".join(chunks)

            try:
                candidate = Solution.model_validate_json(raw)
            except ValidationError as exc:
                logger.warning("streamed reply did not validate: %s", exc)
                if solution is not None:
                    break
                yield SolveEvent(
                    type="error",
                    message="The model's reply did not match the solution schema.",
                )
                return

            yield SolveEvent(
                type="stage", stage="verifying", message="Checking the answer with SymPy"
            )
            candidate_verdict = await verify(candidate.verification)

            # Keep the first result, and replace it only if a retry verifies.
            if solution is None or candidate_verdict.ok:
                solution, verdict = candidate, candidate_verdict

            if candidate_verdict.kind is not VerdictKind.REFUTED or attempt_no == 2:
                break

            messages = [
                Message(role="user", content=problem),
                Message(role="assistant", content=raw),
                Message(
                    role="user",
                    content=RETRY_SUFFIX.format(
                        claimed=candidate_verdict.claimed or "(unclear)",
                        expected=candidate_verdict.expected or "(see detail)",
                        detail=candidate_verdict.detail,
                    ),
                ),
            ]

        assert solution is not None and verdict is not None  # loop guarantees both

        yield SolveEvent(
            type="result",
            payload={
                "verified": verdict.ok,
                "solution": solution.model_dump(mode="json"),
                "verdict": {
                    "kind": verdict.kind.value,
                    "detail": verdict.detail,
                    "expected": verdict.expected,
                    "claimed": verdict.claimed,
                    "checks": verdict.checks,
                },
                "total_ms": round((time.perf_counter() - started) * 1000, 1),
            },
        )
