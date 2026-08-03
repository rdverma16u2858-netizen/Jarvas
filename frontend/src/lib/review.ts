/**
 * Review API client — mistake detection.
 * ═══════════════════════════════════════════════════════════════════════
 *
 * Mirrors backend/app/api/routes/review.py.
 *
 * WHY `studentWasRight` IS `boolean | null` AND NOT `boolean`
 *   `null` means SymPy could not settle it — a proof, an unparseable answer,
 *   a reference answer that itself failed to verify. That is genuinely
 *   different from "no", and typing it as a plain boolean would let a
 *   component render "incorrect" for a question that was never decided.
 *
 *   The compiler forces every caller to handle the third state.
 */

import { apiFetch } from "./api";

export type ErrorType =
  | "arithmetic"
  | "sign"
  | "algebraic"
  | "conceptual"
  | "procedural"
  | "domain"
  | "incomplete"
  | "notation";

export type Severity = "fatal" | "minor";

export type ReviewVerdict =
  | "correct"
  | "right_answer_flawed_working"
  | "wrong"
  | "incomplete"
  | "unclear";

export interface Mistake {
  line: number;
  quote: string;
  type: ErrorType;
  severity: Severity;
  what_went_wrong: string;
  why_it_is_wrong: string;
  correction: string;
}

export interface Review {
  student_answer: string;
  mistakes: Mistake[];
  verdict: ReviewVerdict;
  summary: string;
  what_went_well: string;
  corrected_working: string[];
  correct_answer: string;
  correct_answer_latex: string;
  topic: string;
  difficulty: string;
  concept_to_review: string;
}

export interface CheckVerdict {
  kind: "verified" | "refuted" | "unverifiable" | "error";
  detail: string;
  expected: string;
  claimed: string;
  checks: string[];
}

export interface ReviewResponse {
  /** null when SymPy could not determine it — NOT the same as false. */
  student_was_right: boolean | null;
  verdict: ReviewVerdict;
  /** Whether the reference answer this review rests on was itself checked. */
  verified: boolean;
  /** Set when SymPy contradicted the reviewer and the verdict was corrected. */
  overridden_from: ReviewVerdict | null;
  review: Review;
  answer_check: CheckVerdict;
  student_check: CheckVerdict;
  review_id: number | null;
  model: string;
  total_ms: number;
}

export interface ReviewSummary {
  id: number;
  problem: string;
  verdict: ReviewVerdict;
  topic: string;
  difficulty: string;
  mistake_count: number;
  error_types: ErrorType[];
  verified: boolean;
  overridden_from: string;
  created_at: string;
}

export interface MistakePatterns {
  reviews: number;
  mistakes: number;
  by_error_type: { type: ErrorType; count: number }[];
  by_topic: { topic: string; mistakes: number }[];
  by_verdict: { verdict: ReviewVerdict; count: number }[];
  /** null until there are enough mistakes to call it a pattern. */
  most_common_error: ErrorType | null;
}

// ── requests ──────────────────────────────────────────────────────────────

export function reviewWorking(
  problem: string,
  working: string,
  { tier = "balanced", signal }: { tier?: string; signal?: AbortSignal } = {},
): Promise<ReviewResponse> {
  return apiFetch<ReviewResponse>("/review", {
    method: "POST",
    body: JSON.stringify({ problem, working, tier }),
    signal,
  });
}

export function getPatterns(): Promise<MistakePatterns> {
  return apiFetch<MistakePatterns>("/review/patterns");
}

export function getReviewHistory(): Promise<ReviewSummary[]> {
  return apiFetch<ReviewSummary[]>("/review/history");
}

export function deleteReview(id: number): Promise<void> {
  return apiFetch<void>(`/review/${id}`, { method: "DELETE" });
}

// ── display ───────────────────────────────────────────────────────────────

/**
 * How each verdict is presented.
 *
 * `right_answer_flawed_working` reads as a PASS, not a failure — the student
 * got the right answer. The caveat about the working is secondary and styled
 * that way, because leading with the criticism would land as "you were
 * wrong", which is the misreading this phase is built to avoid.
 */
export const VERDICT_STYLE: Record<
  ReviewVerdict,
  { label: string; tone: "good" | "mixed" | "bad" | "neutral"; blurb: string }
> = {
  correct: {
    label: "Correct",
    tone: "good",
    blurb: "The answer is right and the working is sound.",
  },
  right_answer_flawed_working: {
    label: "Right answer",
    tone: "mixed",
    blurb: "You reached the correct answer, but something in the working needs attention.",
  },
  wrong: {
    label: "Not correct",
    tone: "bad",
    blurb: "The answer does not match. Here is where it turned.",
  },
  incomplete: {
    label: "Unfinished",
    tone: "neutral",
    blurb: "The working stops before reaching an answer.",
  },
  unclear: {
    label: "Could not follow",
    tone: "neutral",
    blurb: "The working could not be read well enough to mark.",
  },
};

export const ERROR_LABEL: Record<ErrorType, string> = {
  arithmetic: "arithmetic slip",
  sign: "sign error",
  algebraic: "algebra error",
  conceptual: "concept misunderstood",
  procedural: "wrong procedure",
  domain: "domain or constraint",
  incomplete: "left unfinished",
  notation: "notation",
};
