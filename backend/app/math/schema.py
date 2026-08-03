"""
The shape of a solution.
═══════════════════════════════════════════════════════════════════════════

WHY A SCHEMA INSTEAD OF FREE TEXT
    Asking a model for "a step-by-step solution" gets prose whose structure
    changes every call — sometimes numbered steps, sometimes paragraphs,
    sometimes a summary at the top. You cannot render that consistently, store
    it usefully, or check it.

    Declaring the shape here and handing it to the model as a JSON Schema
    makes every solution the same object, so the frontend, the database and
    the verifier all know what they are getting.

THE `verification` FIELD IS THE POINT
    Everything else is for the student. `verification` is for SymPy: a
    machine-checkable restatement of the answer, in a form that can be
    independently recomputed.

    This is what closes the gap that Phase 1 exposed. The model produced the
    correct closed form for an integral and then wrote the decimal expansion
    wrong from the 8th significant figure — prose that reads perfectly and is
    quietly incorrect. No amount of "explain carefully" fixes that; only
    recomputing it does.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class Topic(str, Enum):
    """The syllabus. Used for progress tracking and question generation later."""

    ALGEBRA = "algebra"
    LINEAR_ALGEBRA = "linear_algebra"
    CALCULUS = "calculus"
    MULTIVARIABLE_CALCULUS = "multivariable_calculus"
    DIFFERENTIAL_EQUATIONS = "differential_equations"
    INTEGRAL_CALCULUS = "integral_calculus"
    VECTOR_CALCULUS = "vector_calculus"
    PROBABILITY = "probability"
    STATISTICS = "statistics"
    DISCRETE_MATHEMATICS = "discrete_mathematics"
    NUMBER_THEORY = "number_theory"
    COMBINATORICS = "combinatorics"
    GRAPH_THEORY = "graph_theory"
    REAL_ANALYSIS = "real_analysis"
    COMPLEX_ANALYSIS = "complex_analysis"
    ABSTRACT_ALGEBRA = "abstract_algebra"
    OTHER = "other"


class Difficulty(str, Enum):
    """Difficulty bands, matching how Indian exam prep actually talks about it."""

    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"
    JEE_MAIN = "jee_main"
    JEE_ADVANCED = "jee_advanced"
    OLYMPIAD = "olympiad"
    UNIVERSITY = "university"


class ClaimKind(str, Enum):
    """What kind of assertion the verifier should check.

    NONE is not a failure — it is the honest answer for a proof, a word
    problem, or anything with no single computable result. Pretending those
    are verified would be worse than admitting they are not.
    """

    DEFINITE_INTEGRAL = "definite_integral"
    INDEFINITE_INTEGRAL = "indefinite_integral"
    DERIVATIVE = "derivative"
    LIMIT = "limit"
    EQUATION_ROOTS = "equation_roots"
    EXPRESSION_EQUALITY = "expression_equality"
    NUMERIC = "numeric"
    NONE = "none"


# NOTE FOR MAINTAINERS — the docstrings on the models below are NOT internal
# documentation. Pydantic copies a model's docstring into its JSON Schema
# `description`, which is handed to the LLM as part of the request. Anything
# written here is prompt text: it costs tokens on every call and the model
# will try to follow it.
#
# So implementation notes live in comments like this one, and docstrings are
# written for the model.
#
# Design note on Verification: it is deliberately FLAT — one object with
# optional fields rather than a discriminated union per claim kind. Gemini's
# responseSchema is a subset of JSON Schema with no $ref and poor union
# support, so a nested union is rejected or silently mangled. A flat object of
# plain strings survives the round trip intact.


class Verification(BaseModel):
    """A restatement of the answer that a computer algebra system can check.

    Write every expression in SymPy syntax, not LaTeX: x**2 not x^2,
    log(x) for natural log, sqrt(x), exp(x), and pi / E / oo for the
    constants. Give exact values rather than rounded decimals.
    """

    kind: ClaimKind = Field(
        description="Which check applies. Use 'none' for proofs or anything with no single computable result."
    )
    expression: str = Field(
        default="",
        description=(
            "The subject in SymPy syntax: the integrand, the function being "
            "differentiated, the equation set to zero, or the expression whose "
            "limit is taken. Empty when kind is 'none'."
        ),
    )
    variable: str = Field(
        default="x", description="Variable of integration/differentiation, e.g. 'x'"
    )
    lower: str = Field(
        default="",
        description="Lower limit for a definite integral, or the point a limit approaches.",
    )
    upper: str = Field(default="", description="Upper limit for a definite integral.")
    result: str = Field(
        default="",
        description=(
            "The claimed answer in SymPy syntax, EXACT where possible: write "
            "'pi*log(2)/8' rather than a rounded decimal. An exact form can be "
            "checked precisely; a decimal cannot."
        ),
    )
    roots: list[str] = Field(
        default_factory=list,
        description="For equation_roots: every solution, each in SymPy syntax.",
    )


# `justification` is required on purpose: "why is this step legal?" is the
# difference between a worked example a student learns from and a sequence of
# expressions they can only take on faith.
class Step(BaseModel):
    """One step of the derivation, small enough to follow in a single read."""

    number: int = Field(description="1-based step index")
    action: str = Field(description="What is done in this step, in plain words")
    expression: str = Field(
        default="",
        description="The resulting expression in LaTeX, without surrounding $ signs",
    )
    justification: str = Field(
        description="Why this step is valid — the rule, theorem or condition that permits it"
    )


# Field order matters: it becomes `propertyOrdering` in the Gemini schema, and
# generating the reasoning steps BEFORE the final answer improves accuracy — a
# model made to commit to an answer first will rationalise toward it.
class Solution(BaseModel):
    """A complete worked solution to one mathematics problem."""

    # ── the work ──────────────────────────────────────────────────────────
    topic: Topic = Field(description="Primary topic this problem belongs to")
    steps: list[Step] = Field(
        description="The derivation, in order. Each step small enough to follow."
    )
    formulas_used: list[str] = Field(
        default_factory=list,
        description="Named formulas, theorems or identities applied, in LaTeX",
    )

    # ── the answer ────────────────────────────────────────────────────────
    final_answer: str = Field(description="The answer in plain text, stated exactly")
    answer_latex: str = Field(
        default="", description="The same answer as LaTeX, without surrounding $ signs"
    )

    # ── the teaching ──────────────────────────────────────────────────────
    common_mistakes: list[str] = Field(
        default_factory=list,
        description="Specific errors students make ON THIS problem — not generic advice",
    )
    alternative_method: str = Field(
        default="",
        description="A genuinely different route to the same answer, or '' if there is none",
    )
    concepts: list[str] = Field(
        default_factory=list, description="Concepts a student must know to solve this"
    )
    practice_question: str = Field(
        default="",
        description="One similar problem to try next, testing the same idea at similar difficulty",
    )

    # ── metadata ──────────────────────────────────────────────────────────
    difficulty: Difficulty = Field(description="Difficulty band")
    time_minutes: int = Field(
        default=5, ge=1, le=180, description="Realistic solving time for a prepared student"
    )

    # ── the check ─────────────────────────────────────────────────────────
    verification: Verification = Field(
        description="Machine-checkable restatement of the answer, for independent verification"
    )


# ─────────────────────────────────────────────────────────────────────────
#  Gemini schema conversion
# ─────────────────────────────────────────────────────────────────────────

# Keys Gemini's responseSchema understands. Anything else is dropped rather
# than sent, because unknown keys cause a 400 rather than being ignored.
_ALLOWED = {
    "type",
    "description",
    "enum",
    "items",
    "properties",
    "required",
    "nullable",
    "format",
    "propertyOrdering",
}


def _resolve(node: Any, defs: dict[str, Any]) -> Any:
    """Inline every $ref and strip keys Gemini rejects.

    Pydantic emits nested models as `$ref` pointers into a `$defs` block.
    Gemini's schema dialect has no `$ref` at all, so the tree must be
    flattened before it is sent — passing Pydantic's raw output straight
    through fails with an unhelpful 400.
    """
    if isinstance(node, list):
        return [_resolve(item, defs) for item in node]
    if not isinstance(node, dict):
        return node

    # Follow a $ref to its definition and resolve that instead.
    if "$ref" in node:
        name = node["$ref"].removeprefix("#/$defs/")
        return _resolve(defs.get(name, {}), defs)

    # Optional[X] becomes anyOf[X, null]. Gemini has no anyOf: take the real
    # branch and mark it nullable.
    if "anyOf" in node:
        options = [o for o in node["anyOf"] if o.get("type") != "null"]
        resolved = _resolve(options[0], defs) if options else {"type": "string"}
        if len(options) < len(node["anyOf"]):
            resolved["nullable"] = True
        if "description" in node:
            resolved["description"] = node["description"]
        return resolved

    out: dict[str, Any] = {}
    for key, value in node.items():
        if key not in _ALLOWED:
            continue  # drops title, default, $schema, additionalProperties...

        if key == "properties":
            # `properties` is a MAP OF FIELD NAMES to schemas, not a schema.
            # Recursing into it wholesale would filter the field names
            # themselves against the allow-list and delete every one — which
            # produced a Gemini 400 reading "required[0]: property is not
            # defined", because `required` listed fields that no longer
            # existed. Recurse into the values, keep the keys.
            out[key] = {field: _resolve(subschema, defs) for field, subschema in value.items()}
        elif key == "required":
            out[key] = list(value)  # a list of names; nothing to resolve
        else:
            out[key] = _resolve(value, defs)

    # Preserve declaration order so the model generates fields in the order
    # the schema lists them - reasoning first, answer after.
    if "properties" in out and isinstance(out["properties"], dict):
        out["propertyOrdering"] = list(out["properties"].keys())

    return out


def to_gemini_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Convert a Pydantic model into a Gemini-compatible responseSchema."""
    raw = model.model_json_schema()
    defs = raw.pop("$defs", {})
    return _resolve(raw, defs)


SOLUTION_SCHEMA = to_gemini_schema(Solution)
