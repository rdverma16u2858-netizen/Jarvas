/**
 * Practice API client — question generation and the question bank.
 * ═══════════════════════════════════════════════════════════════════════
 *
 * Mirrors backend/app/api/routes/generate.py.
 *
 * WHY THE ANSWER FIELDS ARE `| null`
 *   They are not optional in the "sometimes the backend forgets" sense —
 *   they are deliberately withheld until the student asks. Typing them as
 *   nullable makes that a fact the compiler enforces: any component that
 *   wants to render an answer has to handle not having one, which is the
 *   state it will be in for most of the question's life.
 *
 * WHY THE VOCABULARY IS FETCHED RATHER THAN HARDCODED
 *   The 16 topics, 7 difficulties and 6 types are Python enums. A second
 *   copy in TypeScript drifts the first time one is added, and the failure is
 *   a 422 from a dropdown the user can see but the API rejects.
 */

import { apiFetch } from "./api";

export type QuestionType =
  | "multiple_choice"
  | "multiple_correct"
  | "numerical"
  | "short_answer"
  | "proof"
  | "true_false";

/** Types that come with an option list. Everything else is written out. */
export const CHOICE_TYPES: ReadonlySet<string> = new Set([
  "multiple_choice",
  "multiple_correct",
]);

export interface PracticeQuestion {
  id: number | null;
  number: number;
  type: QuestionType;
  topic: string;
  difficulty: string;
  prompt: string;
  options: string[];
  hint: string;
  concepts: string[];
  time_minutes: number;

  /** Whether SymPy independently recomputed the answer key. */
  verified: boolean;
  verdict_kind: string;

  // ── withheld until revealed ────────────────────────────────────────────
  answer: string | null;
  answer_latex: string | null;
  correct_options: number[] | null;
  solution_outline: string[] | null;

  attempts: number;
  /** Running total of correct attempts — not the verdict on the latest one. */
  correct: number;
  bookmarked: boolean;

  /** Set only on the response to an attempt: whether THAT attempt was right. */
  was_correct?: boolean | null;
}

export interface GenerateResponse {
  questions: PracticeQuestion[];
  confirmed: number;
  /** Answer keys SymPy contradicted and discarded. Non-zero is the system
   *  working, not failing. */
  rejected: number;
  model: string;
  total_ms: number;
}

export interface Vocabulary {
  topics: string[];
  difficulties: string[];
  types: string[];
  max_count: number;
}

export interface TopicStats {
  topic: string;
  questions: number;
  attempts: number;
  correct: number;
  /** null when nothing has been attempted — not the same as 0% correct. */
  accuracy: number | null;
}

// ── generation ────────────────────────────────────────────────────────────

export interface GenerateOptions {
  topic: string;
  difficulty: string;
  type: string;
  count: number;
  concepts?: string;
  tier?: string;
  signal?: AbortSignal;
}

export function generateQuestions({
  signal,
  ...body
}: GenerateOptions): Promise<GenerateResponse> {
  return apiFetch<GenerateResponse>("/generate", {
    method: "POST",
    body: JSON.stringify(body),
    signal,
  });
}

export function getVocabulary(): Promise<Vocabulary> {
  return apiFetch<Vocabulary>("/generate/topics");
}

export function getStats(): Promise<TopicStats[]> {
  return apiFetch<TopicStats[]>("/generate/stats");
}

// ── the bank ──────────────────────────────────────────────────────────────

export interface BankFilters {
  topic?: string;
  difficulty?: string;
  type?: string;
  verifiedOnly?: boolean;
  unattemptedOnly?: boolean;
  bookmarkedOnly?: boolean;
}

export function listQuestions(filters: BankFilters = {}): Promise<PracticeQuestion[]> {
  const params = new URLSearchParams();
  if (filters.topic) params.set("topic", filters.topic);
  if (filters.difficulty) params.set("difficulty", filters.difficulty);
  if (filters.type) params.set("type", filters.type);
  if (filters.verifiedOnly) params.set("verified_only", "true");
  if (filters.unattemptedOnly) params.set("unattempted_only", "true");
  if (filters.bookmarkedOnly) params.set("bookmarked_only", "true");
  return apiFetch<PracticeQuestion[]>(`/generate/questions?${params}`);
}

/** The deliberate reveal — called when the student presses "show answer". */
export function revealAnswer(questionId: number): Promise<PracticeQuestion> {
  return apiFetch<PracticeQuestion>(
    `/generate/questions/${questionId}?include_answers=true`,
  );
}

/**
 * Record an attempt. Returns the graded question, answer included — once the
 * student has committed there is nothing left to withhold.
 *
 * Choice questions send `selected` and are graded by the SERVER, because the
 * client has never been given `correct_options` and cannot mark them. Written
 * questions send the student's own `correct` verdict, which is the only thing
 * that can mark "state the domain of f" honestly.
 */
export function recordAttempt(
  questionId: number,
  grade: { selected: number[] } | { correct: boolean },
): Promise<PracticeQuestion> {
  return apiFetch<PracticeQuestion>(`/generate/questions/${questionId}/attempt`, {
    method: "POST",
    body: JSON.stringify(grade),
  });
}

export function bookmarkQuestion(
  questionId: number,
  bookmarked: boolean,
): Promise<PracticeQuestion> {
  return apiFetch<PracticeQuestion>(`/generate/questions/${questionId}/bookmark`, {
    method: "POST",
    body: JSON.stringify({ bookmarked }),
  });
}

export function deleteQuestion(questionId: number): Promise<void> {
  return apiFetch<void>(`/generate/questions/${questionId}`, { method: "DELETE" });
}

// ── display helpers ───────────────────────────────────────────────────────

/** "jee_advanced" -> "jee advanced" */
export function humanise(value: string): string {
  return value.replace(/_/g, " ");
}
