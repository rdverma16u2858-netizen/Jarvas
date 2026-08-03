/**
 * Quiz API client — quizzes and mock tests.
 * ═══════════════════════════════════════════════════════════════════════
 *
 * Mirrors backend/app/api/routes/quiz.py.
 *
 * WHO OWNS THE CLOCK
 *   The server. Every response carries `seconds_remaining`, computed from a
 *   stored `started_at`. The client ticks down locally between requests so
 *   the countdown is smooth, and RESYNCS from the server on every answer —
 *   so a sleeping tab, a suspended laptop or a page reload cannot gain or
 *   lose time. The local tick is a display convenience, never the truth.
 *
 * WHY THE ANSWER FIELDS ARE `| null`
 *   They are absent for the entire life of a running paper and only appear
 *   once it has been marked. Typing them nullable makes the component handle
 *   the state it will actually be in most of the time.
 */

import { apiFetch } from "./api";

export type QuizMode = "practice" | "mock_test";
export type QuizStatus = "in_progress" | "submitted" | "expired";

export interface QuizQuestion {
  id: number;
  position: number;
  type: string;
  topic: string;
  difficulty: string;
  prompt: string;
  options: string[];
  time_minutes: number;

  // what the student has put so far
  selected: number[];
  written: string;
  self_marked: boolean | null;

  // ── only once marked ───────────────────────────────────────────────────
  answer: string | null;
  answer_latex: string | null;
  correct_options: number[] | null;
  solution_outline: string[] | null;
  is_correct: boolean | null;
  marks: number | null;
}

export interface Quiz {
  id: number;
  title: string;
  mode: QuizMode;
  status: QuizStatus;
  topic: string;
  difficulty: string;

  question_count: number;
  marks_correct: number;
  marks_wrong: number;

  time_limit_seconds: number;
  /** null when untimed. The server's figure — always prefer it to a local tick. */
  seconds_remaining: number | null;
  elapsed_seconds: number;

  score: number;
  max_score: number;
  percent: number | null;
  correct_count: number;
  wrong_count: number;
  unattempted_count: number;
  /** Correct as a fraction of ATTEMPTED — a different question from percent. */
  accuracy: number | null;

  questions: QuizQuestion[];
}

export interface Availability {
  available: number;
  max_questions: number;
}

export interface QuizStats {
  quizzes: number;
  average_percent: number | null;
  best_percent: number | null;
  recent: {
    id: number;
    title: string;
    mode: QuizMode;
    score: number;
    max_score: number;
    percent: number;
    accuracy: number | null;
    submitted_at: string;
  }[];
}

// ── requests ──────────────────────────────────────────────────────────────

export interface CreateQuizOptions {
  count: number;
  mode: QuizMode;
  topic?: string;
  difficulty?: string;
  type?: string;
  time_limit_seconds?: number;
}

export function createQuiz(options: CreateQuizOptions): Promise<Quiz> {
  return apiFetch<Quiz>("/quiz", {
    method: "POST",
    body: JSON.stringify(options),
  });
}

export function getQuiz(id: number): Promise<Quiz> {
  return apiFetch<Quiz>(`/quiz/${id}`);
}

export function listQuizzes(): Promise<Quiz[]> {
  return apiFetch<Quiz[]>("/quiz");
}

export function getAvailability(filters: {
  topic?: string;
  difficulty?: string;
  type?: string;
}): Promise<Availability> {
  const params = new URLSearchParams();
  if (filters.topic) params.set("topic", filters.topic);
  if (filters.difficulty) params.set("difficulty", filters.difficulty);
  if (filters.type) params.set("type", filters.type);
  return apiFetch<Availability>(`/quiz/available?${params}`);
}

export function getQuizStats(): Promise<QuizStats> {
  return apiFetch<QuizStats>("/quiz/stats");
}

/** Records an answer and returns the resynced quiz — including the clock. */
export function answerQuestion(
  quizId: number,
  questionId: number,
  answer: { selected?: number[]; written?: string; self_marked?: boolean },
): Promise<Quiz> {
  return apiFetch<Quiz>(`/quiz/${quizId}/answer`, {
    method: "POST",
    body: JSON.stringify({ question_id: questionId, ...answer }),
  });
}

export function submitQuiz(quizId: number): Promise<Quiz> {
  return apiFetch<Quiz>(`/quiz/${quizId}/submit`, { method: "POST" });
}

export function deleteQuiz(quizId: number): Promise<void> {
  return apiFetch<void>(`/quiz/${quizId}`, { method: "DELETE" });
}

// ── display ───────────────────────────────────────────────────────────────

/** 540 -> "9:00", 3661 -> "1:01:01" */
export function formatClock(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  const hours = Math.floor(s / 3600);
  const minutes = Math.floor((s % 3600) / 60);
  const secs = s % 60;
  const pad = (n: number) => String(n).padStart(2, "0");
  return hours > 0
    ? `${hours}:${pad(minutes)}:${pad(secs)}`
    : `${minutes}:${pad(secs)}`;
}

/** When the clock should start looking urgent. */
export const CLOCK_WARNING_SECONDS = 60;
