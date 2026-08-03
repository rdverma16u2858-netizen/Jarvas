"""
Tests for the SymPy verification layer.
═══════════════════════════════════════════════════════════════════════════

THE TEST THAT MATTERS MOST
    `test_catches_the_real_phase_1_error` uses the exact wrong decimal a live
    model produced during Phase 1: it derived (pi*ln2)/8 correctly and then
    wrote 0.2721982973 instead of 0.2721982613 — wrong from the 8th
    significant figure, in prose that read perfectly.

    That is the failure this whole layer exists to catch. If that test ever
    goes green-to-red, the product's core promise is broken.

WHY REFUTATION IS TESTED AS HARD AS VERIFICATION
    A verifier that says "verified" to everything passes every happy-path
    test and is worthless. Half of these assert that WRONG answers are
    rejected.
"""

import pytest

from app.math.schema import ClaimKind, Verification
from app.math.verifier import ParseError, VerdictKind, verify, verify_sync

pytestmark = pytest.mark.asyncio


def claim(kind: ClaimKind, **kwargs) -> Verification:
    return Verification(kind=kind, **kwargs)


# ── the headline case ─────────────────────────────────────────────────────


async def test_catches_the_real_phase_1_error() -> None:
    """The exact wrong decimal a live model gave us. Must be REFUTED."""
    verdict = verify_sync(
        claim(ClaimKind.NUMERIC, expression="pi*log(2)/8", result="0.2721982973")
    )

    assert verdict.kind is VerdictKind.REFUTED
    assert verdict.ok is False
    # The correct value must be reported, or the verdict is not actionable.
    assert "0.272198261" in verdict.expected


async def test_accepts_the_correct_value() -> None:
    verdict = verify_sync(
        claim(ClaimKind.NUMERIC, expression="pi*log(2)/8", result="0.2721982613")
    )

    assert verdict.kind is VerdictKind.VERIFIED
    assert verdict.ok is True


# ── definite integrals ────────────────────────────────────────────────────


async def test_hard_integral_verifies_quickly() -> None:
    """The integral SymPy cannot integrate symbolically in 90+ seconds.

    Numerical quadrature answers it in milliseconds, which is why the checker
    does not call sympy.integrate here.
    """
    verdict = verify_sync(
        claim(
            ClaimKind.DEFINITE_INTEGRAL,
            expression="log(1+x)/(1+x**2)",
            variable="x",
            lower="0",
            upper="1",
            result="pi*log(2)/8",
        )
    )

    assert verdict.kind is VerdictKind.VERIFIED


async def test_wrong_definite_integral_is_refuted() -> None:
    verdict = verify_sync(
        claim(
            ClaimKind.DEFINITE_INTEGRAL,
            expression="x**2",
            variable="x",
            lower="0",
            upper="1",
            result="1/2",  # the true value is 1/3
        )
    )

    assert verdict.kind is VerdictKind.REFUTED


async def test_improper_integral_with_infinite_limits() -> None:
    """Gaussian integral — infinite bounds are common in this syllabus."""
    verdict = verify_sync(
        claim(
            ClaimKind.DEFINITE_INTEGRAL,
            expression="exp(-x**2)",
            variable="x",
            lower="-oo",
            upper="oo",
            result="sqrt(pi)",
        )
    )

    assert verdict.kind is VerdictKind.VERIFIED


async def test_integrand_with_free_parameters_is_unverifiable_not_wrong() -> None:
    """A symbolic parameter means there is no single number to check.

    Reporting REFUTED here would accuse a correct answer.
    """
    verdict = verify_sync(
        claim(
            ClaimKind.DEFINITE_INTEGRAL,
            expression="a*x",
            variable="x",
            lower="0",
            upper="1",
            result="a/2",
        )
    )

    assert verdict.kind is VerdictKind.UNVERIFIABLE


# ── indefinite integrals ──────────────────────────────────────────────────


async def test_antiderivative_is_checked_by_differentiating() -> None:
    verdict = verify_sync(
        claim(
            ClaimKind.INDEFINITE_INTEGRAL,
            expression="x**2",
            variable="x",
            result="x**3/3",
        )
    )

    assert verdict.kind is VerdictKind.VERIFIED


async def test_wrong_antiderivative_is_refuted() -> None:
    verdict = verify_sync(
        claim(
            ClaimKind.INDEFINITE_INTEGRAL,
            expression="x**2",
            variable="x",
            result="x**2/2",  # that is the integral of x, not x**2
        )
    )

    assert verdict.kind is VerdictKind.REFUTED
    assert "x**2" in verdict.expected


async def test_antiderivative_differing_by_a_constant_still_verifies() -> None:
    """F(x) and F(x)+7 are both valid antiderivatives.

    Differentiating (rather than comparing to a canonical form) is what makes
    this work — a naive string or symbolic comparison would reject it.
    """
    verdict = verify_sync(
        claim(
            ClaimKind.INDEFINITE_INTEGRAL,
            expression="cos(x)",
            variable="x",
            result="sin(x) + 7",
        )
    )

    assert verdict.kind is VerdictKind.VERIFIED


# ── equations ─────────────────────────────────────────────────────────────


async def test_all_roots_present_verifies() -> None:
    verdict = verify_sync(
        claim(ClaimKind.EQUATION_ROOTS, expression="x**2-4", variable="x", roots=["2", "-2"])
    )

    assert verdict.kind is VerdictKind.VERIFIED


async def test_missing_a_root_is_refuted() -> None:
    """Giving one root of x²=4 is a wrong answer, not a partial one.

    A student told "x = 2" has been taught something false by omission, so
    substitution alone is not enough — completeness is checked too.
    """
    verdict = verify_sync(
        claim(ClaimKind.EQUATION_ROOTS, expression="x**2-4", variable="x", roots=["2"])
    )

    assert verdict.kind is VerdictKind.REFUTED
    assert "incomplete" in verdict.detail


async def test_a_root_that_does_not_satisfy_is_refuted() -> None:
    verdict = verify_sync(
        claim(ClaimKind.EQUATION_ROOTS, expression="x**2-4", variable="x", roots=["2", "3"])
    )

    assert verdict.kind is VerdictKind.REFUTED


# ── derivatives and limits ────────────────────────────────────────────────


async def test_derivative_verifies() -> None:
    verdict = verify_sync(
        claim(
            ClaimKind.DERIVATIVE,
            expression="sin(x)*x**2",
            variable="x",
            result="x**2*cos(x) + 2*x*sin(x)",
        )
    )

    assert verdict.kind is VerdictKind.VERIFIED


async def test_limit_verifies() -> None:
    verdict = verify_sync(
        claim(
            ClaimKind.LIMIT,
            expression="sin(x)/x",
            variable="x",
            lower="0",
            result="1",
        )
    )

    assert verdict.kind is VerdictKind.VERIFIED


async def test_equivalent_expressions_in_different_forms_verify() -> None:
    """sin(2x) and 2·sin(x)·cos(x) are the same function written two ways."""
    verdict = verify_sync(
        claim(
            ClaimKind.EXPRESSION_EQUALITY,
            expression="sin(2*x)",
            result="2*sin(x)*cos(x)",
        )
    )

    assert verdict.kind is VerdictKind.VERIFIED


# ── honesty and safety ────────────────────────────────────────────────────


async def test_proofs_report_unverifiable_rather_than_verified() -> None:
    """Claiming a proof was 'verified' would be a lie the UI would repeat."""
    verdict = verify_sync(claim(ClaimKind.NONE))

    assert verdict.kind is VerdictKind.UNVERIFIABLE
    assert verdict.ok is False, "unverifiable must NOT count as success"


async def test_unparseable_expression_is_an_error_not_a_refutation() -> None:
    """Refuting an answer because the verifier could not read it would be unjust."""
    verdict = verify_sync(
        claim(ClaimKind.DERIVATIVE, expression="x**", variable="x", result="1")
    )

    assert verdict.kind is VerdictKind.ERROR


async def test_code_injection_attempts_are_refused() -> None:
    """Expressions come from an LLM, and SymPy parsing evaluates.

    Without a restricted namespace, `().__class__.__bases__` reaches object
    and from there the import machinery. These must never be parsed.
    """
    from app.math.verifier import _parse

    for hostile in (
        "().__class__.__bases__",
        "__import__('os').system('echo pwned')",
        "x.__class__",
    ):
        with pytest.raises(ParseError):
            _parse(hostile)


async def test_latex_habits_are_tolerated() -> None:
    """Models slip into LaTeX despite the prompt; ^ should not fail the check."""
    verdict = verify_sync(
        claim(ClaimKind.DERIVATIVE, expression="x^2", variable="x", result="2*x")
    )

    assert verdict.kind is VerdictKind.VERIFIED


async def test_async_verify_matches_sync() -> None:
    verdict = await verify(
        claim(ClaimKind.NUMERIC, expression="pi", result="3.14159265358979")
    )

    assert verdict.kind is VerdictKind.VERIFIED
