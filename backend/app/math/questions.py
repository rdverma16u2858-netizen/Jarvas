"""
The shape of a generated practice question.
═══════════════════════════════════════════════════════════════════════════

WHY GENERATED QUESTIONS CARRY A verification OBJECT TOO
    A wrong worked solution is visibly wrong — the student reads the steps and
    something does not follow. A wrong ANSWER KEY is invisible. The student
    works the problem correctly, disagrees with the key, and concludes they
    are the one who is wrong.

    That is a worse failure than anything in Phase 2, and it is why every
    generated question is run through the same SymPy verifier as a solution.
    A question whose answer cannot be confirmed is marked as such, not
    quietly served alongside the confirmed ones.

WHY THE MCQ OPTIONS ARE CHECKED IN PYTHON, NOT BY THE MODEL
    The characteristic failure of generated multiple-choice is an option list
    that does not contain the stated answer, or a `correct_options` index
    pointing at the wrong entry. Neither is a mathematical error, so SymPy
    cannot see it — the maths verifies perfectly while the question is
    unanswerable. `structural_problem` below catches it directly.

SIX TYPES, CHOSEN FOR THE EXAM THESE STUDENTS SIT
    The difficulty bands are already JEE-shaped (jee_main, jee_advanced), so
    the question types follow the same paper: single-correct, multiple-correct
    and numerical-answer are the three objective formats JEE actually uses.
    Short answer, proof and true/false cover university coursework, which the
    same syllabus feeds into.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from app.math.schema import Difficulty, Topic, Verification, to_gemini_schema


class QuestionType(str, Enum):
    """The six formats a practice question can take."""

    MULTIPLE_CHOICE = "multiple_choice"
    MULTIPLE_CORRECT = "multiple_correct"
    NUMERICAL = "numerical"
    SHORT_ANSWER = "short_answer"
    PROOF = "proof"
    TRUE_FALSE = "true_false"


#: Types that must come with an option list. Everything else must not.
CHOICE_TYPES = frozenset({QuestionType.MULTIPLE_CHOICE, QuestionType.MULTIPLE_CORRECT})


# NOTE FOR MAINTAINERS — as in schema.py, the docstrings and Field descriptions
# below are sent to the model as part of the request. They are prompt text, not
# internal documentation. Implementation notes belong in comments like this.
#
# Field order is load-bearing: it becomes `propertyOrdering` in the Gemini
# schema. The question is stated, then reasoned about, and only then answered —
# a model made to emit the answer first will write a question to fit it.


class Question(BaseModel):
    """One practice question with its answer and a machine-checkable claim."""

    # ── the question ──────────────────────────────────────────────────────
    number: int = Field(description="1-based index within the set")
    type: QuestionType = Field(description="Which format this question is in")
    topic: Topic = Field(description="Primary topic")
    difficulty: Difficulty = Field(description="Difficulty band")
    prompt: str = Field(
        description=(
            "The question as a student reads it. Use LaTeX for mathematics, "
            "without surrounding $ signs. State it completely: a question that "
            "needs clarification cannot be answered."
        )
    )
    options: list[str] = Field(
        default_factory=list,
        description=(
            "For multiple_choice and multiple_correct ONLY: exactly four "
            "options, in LaTeX, without labels like 'A)'. The wrong options "
            "must be plausible — each should be the result of a specific "
            "mistake a student actually makes, not a random value. Empty for "
            "every other type."
        ),
    )

    # ── the reasoning, before the answer ──────────────────────────────────
    solution_outline: list[str] = Field(
        default_factory=list,
        description=(
            "The route to the answer in 2-5 short lines. Not a full worked "
            "solution - enough that a student who is stuck can see the method."
        ),
    )
    hint: str = Field(
        default="",
        description="One nudge that points at the method without giving the answer away",
    )

    # ── the answer ────────────────────────────────────────────────────────
    answer: str = Field(
        description=(
            "The correct answer in plain text, stated exactly. For "
            "multiple_choice and multiple_correct this must be the TEXT of the "
            "correct option(s), matching the options list."
        )
    )
    answer_latex: str = Field(
        default="", description="The same answer as LaTeX, without surrounding $ signs"
    )
    correct_options: list[int] = Field(
        default_factory=list,
        description=(
            "0-based indices into `options` that are correct. Exactly one entry "
            "for multiple_choice; one or more for multiple_correct; empty for "
            "every other type."
        ),
    )

    # ── metadata ──────────────────────────────────────────────────────────
    concepts: list[str] = Field(
        default_factory=list, description="Concepts a student needs to solve this"
    )
    time_minutes: int = Field(
        default=5, ge=1, le=180, description="Realistic solving time for a prepared student"
    )

    # ── the check ─────────────────────────────────────────────────────────
    verification: Verification = Field(
        description=(
            "Machine-checkable restatement of the ANSWER, so it can be "
            "independently recomputed. Use kind 'none' for proofs and for "
            "anything with no single computable value."
        )
    )

    def structural_problem(self) -> str:
        """Return why this question is unusable, or "" if it is well formed.

        These are the failures SymPy cannot see. The mathematics can verify
        perfectly while the question itself is impossible to answer — four
        options none of which is the stated answer, or an index pointing past
        the end of the list. Checked here, in Python, because they are facts
        about the object rather than about the maths.
        """
        is_choice = self.type in CHOICE_TYPES

        if is_choice:
            if len(self.options) < 2:
                return f"{self.type.value} needs at least two options, got {len(self.options)}"
            if not self.correct_options:
                return f"{self.type.value} has no correct option marked"
            if any(i < 0 or i >= len(self.options) for i in self.correct_options):
                return (
                    f"correct_options {self.correct_options} points outside "
                    f"the {len(self.options)} options"
                )
            if len(set(self.correct_options)) != len(self.correct_options):
                return f"correct_options {self.correct_options} repeats an index"
            if self.type is QuestionType.MULTIPLE_CHOICE and len(self.correct_options) != 1:
                return (
                    "multiple_choice must have exactly one correct option, got "
                    f"{len(self.correct_options)}"
                )
        else:
            if self.options:
                return f"{self.type.value} must not carry options"
            if self.correct_options:
                return f"{self.type.value} must not mark correct options"

        if not self.prompt.strip():
            return "the question text is empty"
        if not self.answer.strip():
            return "the answer is empty"

        return ""


class QuestionSet(BaseModel):
    """A set of practice questions on one topic."""

    questions: list[Question] = Field(description="The generated questions, in order")


QUESTION_SET_SCHEMA = to_gemini_schema(QuestionSet)
