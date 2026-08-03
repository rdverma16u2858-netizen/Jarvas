"""
Independent verification with SymPy.
═══════════════════════════════════════════════════════════════════════════

WHY THIS EXISTS
    Phase 1 produced this, live, from a working model:

        claimed:  0.2721982973
        true:     0.2721982613

    The model derived the correct closed form for the integral and then wrote
    its decimal expansion wrong from the 8th significant figure, while
    presenting it as exact to 10. The prose was flawless. A student would have
    copied it down and learned a wrong number.

    Nothing in the explanation reveals that. Only recomputing it does.

WHAT VERIFICATION MEANS HERE
    Not "the model sounded confident" and not "a second model agreed" — two
    language models can be wrong together. SymPy computes the answer from the
    problem statement independently, using symbolic algebra, and compares.

    Differentiate the claimed antiderivative and see if the integrand comes
    back. Substitute the claimed roots and see if the equation vanishes.
    Evaluate both sides numerically at high precision.

FOUR OUTCOMES, AND WHY "UNVERIFIABLE" IS HONEST
    VERIFIED     recomputed and matched
    REFUTED      recomputed and did NOT match — the answer is wrong
    UNVERIFIABLE nothing checkable (a proof, a word problem)
    ERROR        could not parse or compute in time

    UNVERIFIABLE is reported plainly rather than dressed up as success. A
    verifier that always says yes is worth nothing.

SECURITY
    Expressions here are LLM output, which is untrusted input. SymPy's
    `sympify` runs `eval`, so feeding it raw model text is remote code
    execution waiting to happen. Parsing goes through `parse_expr` with an
    explicit whitelist namespace — see `_parse`.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import mpmath
import sympy
from mpmath import mp
from sympy.parsing.sympy_parser import (
    implicit_multiplication_application,
    parse_expr,
    standard_transformations,
)

from app.core.logging import get_logger
from app.math.schema import ClaimKind, Verification

logger = get_logger(__name__)


class VerdictKind(str, Enum):
    VERIFIED = "verified"
    REFUTED = "refuted"
    UNVERIFIABLE = "unverifiable"
    ERROR = "error"


@dataclass
class Verdict:
    """The result of checking one claim."""

    kind: VerdictKind
    detail: str = ""
    #: What SymPy computed, when it differs from the claim. This is the value
    #: that makes a REFUTED verdict actionable rather than just discouraging.
    expected: str = ""
    claimed: str = ""
    checks: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True only for VERIFIED. Deliberately strict.

        UNVERIFIABLE and ERROR are NOT successes. Treating them as passes is
        exactly how an unverified wrong answer reaches a student wearing a
        green tick.
        """
        return self.kind is VerdictKind.VERIFIED


# ── safe parsing ──────────────────────────────────────────────────────────

# Only these names exist while parsing. Without a restricted namespace,
# parse_expr would inherit builtins and a crafted expression could import os
# and run commands - the model output is untrusted.
_NAMESPACE: dict[str, Any] = {
    name: getattr(sympy, name)
    for name in (
        "sin cos tan cot sec csc asin acos atan atan2 sinh cosh tanh asinh acosh atanh "
        "exp log sqrt Abs sign floor ceiling factorial gamma erf "
        "pi E I oo zoo nan Rational Integer Float Sum Product "
        "binomial Min Max re im conjugate arg Symbol Eq Matrix"
    ).split()
}
# Common aliases students and models write.
_NAMESPACE["ln"] = sympy.log
_NAMESPACE["Infinity"] = sympy.oo
_NAMESPACE["infinity"] = sympy.oo

_TRANSFORMS = standard_transformations + (implicit_multiplication_application,)

# Ceiling on a single SymPy computation. `integrate` can run for minutes on a
# hard integrand; a student waiting on a spinner cannot.
_TIMEOUT_SECONDS = 12.0


class ParseError(ValueError):
    """The expression could not be turned into a SymPy object."""


def _parse(text: str, *, what: str = "expression") -> sympy.Expr:
    """Parse a model-written expression into SymPy, safely.

    Rejects dunder access outright — `parse_expr` still evaluates, and
    `().__class__.__bases__` is the classic escape from a restricted
    namespace.
    """
    text = (text or "").strip()
    if not text:
        raise ParseError(f"empty {what}")

    if "__" in text or "import" in text.lower():
        raise ParseError(f"refusing to parse suspicious {what}: {text[:60]!r}")

    # Tolerate LaTeX habits the model sometimes slips into despite the prompt.
    cleaned = (
        text.replace("^", "**")
        .replace("\\left", "")
        .replace("\\right", "")
        .replace("\\cdot", "*")
        .replace("{", "(")
        .replace("}", ")")
        .lstrip("\\")
    )

    try:
        return parse_expr(
            cleaned,
            local_dict={},
            global_dict=_NAMESPACE,
            transformations=_TRANSFORMS,
            evaluate=True,
        )
    except Exception as exc:  # noqa: BLE001 — any parse failure is the same to us
        raise ParseError(f"could not parse {what} {text[:60]!r}: {exc}") from exc


def _equal(a: sympy.Expr, b: sympy.Expr) -> bool:
    """Decide whether two expressions are mathematically the same.

    Three escalating attempts, because no single test is reliable:

    1. `simplify(a - b) == 0` — exact, but can be slow or give up.
    2. Numeric sampling — catches cases simplify cannot close, e.g. answers
       written in different but equivalent trigonometric forms.
    3. Direct high-precision evaluation for pure numbers.

    Sampling uses awkward irrational points rather than small integers so a
    coincidental match at x=1 does not pass as equality.
    """
    difference = a - b

    try:
        if sympy.simplify(difference) == 0:
            return True
    except Exception:  # noqa: BLE001
        pass

    try:
        if sympy.N(sympy.Abs(difference), 30) < 1e-20:
            return True
    except Exception:  # noqa: BLE001
        pass

    symbols = sorted(difference.free_symbols, key=str)
    if not symbols:
        return False

    probes = [sympy.Rational(3, 7), sympy.Rational(11, 5), sympy.sqrt(2) / 3]
    matches = 0
    for probe in probes:
        try:
            value = difference.subs(dict.fromkeys(symbols, probe))
            numeric = complex(sympy.N(value, 25))
            if abs(numeric) < 1e-15:
                matches += 1
        except Exception:  # noqa: BLE001
            continue  # undefined at this point; try the next
    # Every point that could be evaluated must agree.
    return matches == len(probes)


# ── the individual checks ─────────────────────────────────────────────────


def _check_definite_integral(claim: Verification) -> Verdict:
    """Compare the claimed value against high-precision numerical quadrature.

    WHY NUMERICAL, NOT SYMBOLIC
        The obvious implementation calls `sympy.integrate` and compares. It
        was tried, and it does not work: on the very integral from Phase 1
        (∫₀¹ ln(1+x)/(1+x²) dx) SymPy ran for over 90 seconds without
        finishing, because the antiderivative needs polylogarithms.

        `mpmath.quad` computes the same value to 30 digits in 0.025s — about
        four thousand times faster, and bounded.

        The insight: verifying a DEFINITE integral does not require the
        antiderivative. It requires the number. Numerical quadrature answers
        exactly the question being asked, and 30 digits is far past what any
        wrong answer would survive.

        (Indefinite integrals are different — there the check is
        differentiation, which is fast and mechanical. See below.)
    """
    var = sympy.Symbol(claim.variable or "x")
    integrand = _parse(claim.expression, what="integrand")
    lower = _parse(claim.lower, what="lower limit")
    upper = _parse(claim.upper, what="upper limit")
    claimed = _parse(claim.result, what="result")

    if integrand.free_symbols - {var}:
        return Verdict(
            kind=VerdictKind.UNVERIFIABLE,
            detail="integrand contains free parameters, so it has no single numeric value",
            claimed=str(claimed),
        )

    with mp.workdps(30):
        try:
            f = sympy.lambdify(var, integrand, "mpmath")
            computed = mpmath.quad(f, [_to_mpf(lower), _to_mpf(upper)])
        except Exception as exc:  # noqa: BLE001
            return Verdict(
                kind=VerdictKind.ERROR,
                detail=f"numerical integration failed: {type(exc).__name__}: {exc}",
                claimed=str(claimed),
            )

        try:
            want = mpmath.mpmathify(str(sympy.N(claimed, 30)))
        except Exception as exc:  # noqa: BLE001
            return Verdict(
                kind=VerdictKind.ERROR,
                detail=f"claimed value is not numeric: {exc}",
                claimed=str(claimed),
            )

        scale = abs(computed) or mpmath.mpf(1)
        difference = abs(computed - want)
        # 1e-12 relative: far tighter than any real error, loose enough that
        # quadrature round-off never causes a false accusation.
        if difference / scale < mpmath.mpf("1e-12"):
            return Verdict(
                kind=VerdictKind.VERIFIED,
                detail=f"integral = {mpmath.nstr(computed, 12)}, matching the claim",
                checks=[
                    f"numerical quadrature of {integrand} over "
                    f"[{lower}, {upper}] = {mpmath.nstr(computed, 15)}"
                ],
            )

        return Verdict(
            kind=VerdictKind.REFUTED,
            detail=(
                f"integral is {mpmath.nstr(computed, 12)}, "
                f"but the answer claims {mpmath.nstr(want, 12)} "
                f"(differs by {mpmath.nstr(difference, 3)})"
            ),
            expected=mpmath.nstr(computed, 15),
            claimed=str(claimed),
        )


def _to_mpf(expr: sympy.Expr) -> Any:
    """Convert a SymPy limit of integration into something mpmath.quad accepts.

    Infinite bounds are common in this syllabus (improper integrals, Gaussian
    integrals, Laplace transforms), and mpmath handles them natively — but
    only via its own infinity object, not SymPy's `oo`.
    """
    if expr == sympy.oo:
        return mpmath.inf
    if expr == -sympy.oo:
        return -mpmath.inf
    return mpmath.mpmathify(str(sympy.N(expr, 30)))


def _check_indefinite_integral(claim: Verification) -> Verdict:
    """Differentiate the claimed antiderivative and compare to the integrand.

    Checking F' = f rather than recomputing ∫f is both faster and stricter:
    differentiation is mechanical, whereas two correct antiderivatives can
    differ by a constant and look unequal.
    """
    var = sympy.Symbol(claim.variable or "x")
    integrand = _parse(claim.expression, what="integrand")
    antiderivative = _parse(claim.result, what="antiderivative")

    derivative = sympy.diff(antiderivative, var)

    if _equal(derivative, integrand):
        return Verdict(
            kind=VerdictKind.VERIFIED,
            detail="d/dx of the answer returns the integrand",
            checks=[f"d/d{var}({antiderivative}) = {sympy.simplify(derivative)}"],
        )

    return Verdict(
        kind=VerdictKind.REFUTED,
        detail=(
            f"differentiating the answer gives {sympy.simplify(derivative)}, "
            f"but the integrand is {integrand}"
        ),
        expected=str(integrand),
        claimed=str(sympy.simplify(derivative)),
    )


def _check_derivative(claim: Verification) -> Verdict:
    var = sympy.Symbol(claim.variable or "x")
    function = _parse(claim.expression, what="function")
    claimed = _parse(claim.result, what="derivative")

    order = 1
    if claim.lower.strip().isdigit():  # `lower` doubles as order for this kind
        order = max(1, int(claim.lower.strip()))

    computed = sympy.diff(function, var, order)

    if _equal(computed, claimed):
        return Verdict(
            kind=VerdictKind.VERIFIED,
            detail=f"derivative is {sympy.simplify(computed)}",
            checks=[f"d/d{var}({function}) = {sympy.simplify(computed)}"],
        )
    return Verdict(
        kind=VerdictKind.REFUTED,
        detail=f"derivative is {sympy.simplify(computed)}, not {claimed}",
        expected=str(sympy.simplify(computed)),
        claimed=str(claimed),
    )


def _check_limit(claim: Verification) -> Verdict:
    var = sympy.Symbol(claim.variable or "x")
    expression = _parse(claim.expression, what="expression")
    point = _parse(claim.lower or "0", what="limit point")
    claimed = _parse(claim.result, what="limit")

    computed = sympy.limit(expression, var, point)

    if _equal(computed, claimed):
        return Verdict(
            kind=VerdictKind.VERIFIED,
            detail=f"limit is {computed}",
            checks=[f"limit({expression}, {var} -> {point}) = {computed}"],
        )
    return Verdict(
        kind=VerdictKind.REFUTED,
        detail=f"limit is {computed}, not {claimed}",
        expected=str(computed),
        claimed=str(claimed),
    )


def _check_equation_roots(claim: Verification) -> Verdict:
    """Substitute each claimed root, and check none are missing.

    Both directions matter. Substitution catches a wrong root; comparing
    counts catches the more common error of finding one solution and stopping
    — a student told x = 2 solves x² = 4 has been taught something false by
    omission.
    """
    var = sympy.Symbol(claim.variable or "x")
    equation = _parse(claim.expression, what="equation")
    if not claim.roots:
        return Verdict(kind=VerdictKind.ERROR, detail="no roots given to check")

    checks: list[str] = []
    for raw in claim.roots:
        root = _parse(raw, what="root")
        residual = sympy.simplify(equation.subs(var, root))
        if sympy.N(sympy.Abs(residual), 25) > 1e-20:
            return Verdict(
                kind=VerdictKind.REFUTED,
                detail=f"{var} = {root} does not satisfy the equation (leaves {residual})",
                claimed=str(root),
                expected="0",
            )
        checks.append(f"{var} = {root} satisfies the equation")

    try:
        actual = sympy.solve(equation, var)
        if len(actual) > len(claim.roots):
            return Verdict(
                kind=VerdictKind.REFUTED,
                detail=(
                    f"the roots given are correct but incomplete: "
                    f"{len(actual)} solutions exist ({actual}), only "
                    f"{len(claim.roots)} were listed"
                ),
                expected=str(actual),
                claimed=str(claim.roots),
            )
    except Exception:  # noqa: BLE001
        pass  # solve() failing does not invalidate roots that substituted cleanly

    return Verdict(
        kind=VerdictKind.VERIFIED,
        detail=f"all {len(claim.roots)} roots satisfy the equation",
        checks=checks,
    )


def _check_expression_equality(claim: Verification) -> Verdict:
    left = _parse(claim.expression, what="left side")
    right = _parse(claim.result, what="right side")

    if _equal(left, right):
        return Verdict(
            kind=VerdictKind.VERIFIED,
            detail="both sides are equivalent",
            checks=[f"simplify(({left}) - ({right})) = 0"],
        )
    return Verdict(
        kind=VerdictKind.REFUTED,
        detail=f"{left} is not equivalent to {right}",
        expected=str(left),
        claimed=str(right),
    )


def _check_numeric(claim: Verification) -> Verdict:
    """Compare a numeric answer against its exact form.

    `expression` holds the exact value (e.g. `pi*log(2)/8`) and `result` the
    decimal. Comparing them is precisely the check the Phase 1 answer failed.
    """
    claimed = _parse(claim.result, what="value")

    if not claim.expression:
        return Verdict(
            kind=VerdictKind.UNVERIFIABLE,
            detail="a bare number with no exact form to check it against",
            claimed=str(claimed),
        )

    exact = _parse(claim.expression, what="exact form")
    difference = abs(complex(sympy.N(exact - claimed, 30)))

    # Relative tolerance, so the test is as strict for 10^-9 as for 10^9.
    scale = abs(complex(sympy.N(exact, 30))) or 1.0
    if difference / scale < 1e-9:
        return Verdict(
            kind=VerdictKind.VERIFIED,
            detail=f"{claimed} matches the exact value {exact}",
            checks=[f"{exact} = {sympy.N(exact, 12)}"],
        )

    return Verdict(
        kind=VerdictKind.REFUTED,
        detail=(
            f"stated {claimed}, but {exact} = {sympy.N(exact, 12)} "
            f"(differs by {difference:.2e})"
        ),
        expected=str(sympy.N(exact, 12)),
        claimed=str(claimed),
    )


_CHECKS = {
    ClaimKind.DEFINITE_INTEGRAL: _check_definite_integral,
    ClaimKind.INDEFINITE_INTEGRAL: _check_indefinite_integral,
    ClaimKind.DERIVATIVE: _check_derivative,
    ClaimKind.LIMIT: _check_limit,
    ClaimKind.EQUATION_ROOTS: _check_equation_roots,
    ClaimKind.EXPRESSION_EQUALITY: _check_expression_equality,
    ClaimKind.NUMERIC: _check_numeric,
}


def verify_sync(claim: Verification) -> Verdict:
    """Run the check. Blocking — call `verify` from async code."""
    if claim.kind is ClaimKind.NONE:
        return Verdict(
            kind=VerdictKind.UNVERIFIABLE,
            detail="no computable claim — a proof or descriptive answer",
        )

    check = _CHECKS.get(claim.kind)
    if check is None:
        return Verdict(kind=VerdictKind.ERROR, detail=f"no checker for {claim.kind}")

    try:
        return check(claim)
    except ParseError as exc:
        return Verdict(kind=VerdictKind.ERROR, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.warning("verifier crashed on %s: %s", claim.kind, exc)
        return Verdict(
            kind=VerdictKind.ERROR, detail=f"{type(exc).__name__}: {str(exc)[:200]}"
        )


async def verify(claim: Verification) -> Verdict:
    """Async wrapper with a timeout.

    SymPy is CPU-bound and synchronous, so it runs in a worker thread to keep
    the event loop responsive.

    KNOWN LIMITATION: a timed-out computation cannot be killed — Python
    threads are not interruptible. The request returns promptly with ERROR
    while the thread finishes in the background. That is an accepted trade for
    a single-user app; a multi-tenant deployment would need a process pool so
    runaway work can be terminated.
    """
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(verify_sync, claim), timeout=_TIMEOUT_SECONDS
        )
    except TimeoutError:
        return Verdict(
            kind=VerdictKind.ERROR,
            detail=f"verification exceeded {_TIMEOUT_SECONDS:.0f}s and was abandoned",
        )
