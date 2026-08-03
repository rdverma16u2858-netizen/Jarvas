"""
The shape of a review of a student's own working.
═══════════════════════════════════════════════════════════════════════════

THE FAILURE THIS PHASE MUST NOT HAVE
    Telling a student their CORRECT work is wrong.

    Every other mistake here is recoverable. A missed error just means the
    student learns it later. But being told, with authority, that sound
    reasoning is flawed teaches them to distrust reasoning that was fine — and
    a student who cannot trust their own correct work has lost the only tool
    they have.

    A language model asked "is this right?" is agreeable and will find faults
    on request. That tendency is precisely wrong here.

HOW THAT IS PREVENTED
    The review carries TWO machine-checkable claims, not one:

        verification   the answer the reviewer says is correct
        student_check  the student's answer restated as an equality against
                       that correct answer

    SymPy evaluates both. If it confirms the correct answer AND confirms the
    student's answer is equivalent to it, then the student was right — and the
    verdict is overridden to say so, whatever the model claimed. See
    `reviewer.py` for the reconciliation.

    This is the same move as Phase 2, pointed at a different target: there,
    SymPy checked the model's answer; here it also defends the student's.

GETTING THE RIGHT ANSWER BY WRONG REASONING IS ITS OWN VERDICT
    A sign error that cancels a second sign error produces a correct answer
    from broken working. Marking that simply "correct" hides a real problem;
    marking it "wrong" is false. It has its own verdict.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from app.math.schema import Difficulty, Topic, Verification, to_gemini_schema


class ErrorType(str, Enum):
    """What kind of mistake it is — the thing worth tracking over time.

    A student who keeps making SIGN errors needs different help from one
    making CONCEPTUAL ones: the first needs to slow down, the second has not
    understood the rule. Phase 8 aggregates these.
    """

    ARITHMETIC = "arithmetic"
    SIGN = "sign"
    ALGEBRAIC = "algebraic"
    CONCEPTUAL = "conceptual"
    PROCEDURAL = "procedural"
    DOMAIN = "domain"
    INCOMPLETE = "incomplete"
    NOTATION = "notation"


class Severity(str, Enum):
    """Whether the mistake changed the answer.

    Kept separate from the error type because a notation slip and a dropped
    factor of two are both worth mentioning, and treating them alike either
    buries the important one or makes the trivial one alarming.
    """

    FATAL = "fatal"
    MINOR = "minor"


class ReviewVerdict(str, Enum):
    CORRECT = "correct"
    RIGHT_ANSWER_FLAWED_WORKING = "right_answer_flawed_working"
    WRONG = "wrong"
    INCOMPLETE = "incomplete"
    UNCLEAR = "unclear"


# NOTE FOR MAINTAINERS — as in schema.py and questions.py, these docstrings and
# Field descriptions are sent to the model as prompt text. Implementation notes
# belong in comments like this one.
#
# Field order becomes `propertyOrdering` in the Gemini schema: the reviewer
# reads the work and locates the fault BEFORE stating a verdict, because a
# model made to judge first will then hunt for evidence to justify the
# judgement.


class Mistake(BaseModel):
    """One specific error in the student's working."""

    line: int = Field(
        default=0,
        description=(
            "Which line of the student's work is wrong, 1-based. Use 0 only if "
            "the error cannot be tied to a particular line."
        ),
    )
    quote: str = Field(
        default="",
        description=(
            "The exact text from the student's work that is wrong, copied "
            "verbatim so they can find it."
        ),
    )
    type: ErrorType = Field(description="The category of mistake")
    severity: Severity = Field(
        description="fatal if it changes the answer, minor if it does not"
    )
    what_went_wrong: str = Field(
        description="What the student did, in plain words. Describe the action, not the person."
    )
    why_it_is_wrong: str = Field(
        description=(
            "The rule, theorem or condition that this breaks. Name it. "
            "'The chain rule requires the derivative of the inner function' "
            "teaches; 'this is incorrect' does not."
        )
    )
    correction: str = Field(
        default="",
        description="The corrected line in LaTeX, without surrounding $ signs",
    )


class Review(BaseModel):
    """A review of one student's attempt at one problem."""

    # ── read the work first ───────────────────────────────────────────────
    student_answer: str = Field(
        default="",
        description=(
            "The final answer the student reached, copied from their work. "
            "Empty if they did not reach one."
        ),
    )
    mistakes: list[Mistake] = Field(
        default_factory=list,
        description=(
            "Every mistake found, in the order they appear. EMPTY if the work "
            "is sound — do not invent faults to seem thorough."
        ),
    )

    # ── then judge ────────────────────────────────────────────────────────
    verdict: ReviewVerdict = Field(
        description=(
            "correct = right answer, sound working · "
            "right_answer_flawed_working = correct answer reached despite an "
            "error · wrong = the answer is wrong · incomplete = did not finish "
            "· unclear = the working could not be followed"
        )
    )
    summary: str = Field(
        description="One or two sentences: what happened, and where it turned."
    )
    what_went_well: str = Field(
        default="",
        description=(
            "Something the student genuinely did right. Not flattery — name the "
            "actual correct move, e.g. 'the substitution was the right choice'. "
            "Leave empty rather than inventing praise."
        ),
    )

    # ── the correct route ─────────────────────────────────────────────────
    corrected_working: list[str] = Field(
        default_factory=list,
        description=(
            "The correct working from the first mistake onward, in LaTeX, one "
            "line each. Not the whole problem from scratch — resume from where "
            "they went wrong, so their own correct work up to that point still "
            "counts."
        ),
    )
    correct_answer: str = Field(description="The correct answer, stated exactly")
    correct_answer_latex: str = Field(
        default="", description="The correct answer in LaTeX, without surrounding $ signs"
    )

    # ── metadata ──────────────────────────────────────────────────────────
    topic: Topic = Field(description="Primary topic of the problem")
    difficulty: Difficulty = Field(description="Difficulty band of the problem")
    concept_to_review: str = Field(
        default="",
        description="The one concept this student should revise, if any",
    )

    # ── the checks ────────────────────────────────────────────────────────
    verification: Verification = Field(
        description="Machine-checkable restatement of the CORRECT answer"
    )
    student_check: Verification = Field(
        description=(
            "A check of whether the STUDENT'S answer is equivalent to the "
            "correct one. Use kind 'expression_equality' with expression = the "
            "student's final answer and result = the correct answer, both in "
            "SymPy syntax. Use kind 'none' only when the student reached no "
            "answer, or their answer is not a mathematical expression."
        )
    )


REVIEW_SCHEMA = to_gemini_schema(Review)
