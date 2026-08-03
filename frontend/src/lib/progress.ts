/**
 * Progress API client.
 * ═══════════════════════════════════════════════════════════════════════
 *
 * Mirrors backend/app/api/routes/progress.py.
 *
 * WHY `suggested` AND `accuracy` ARE NULLABLE
 *   Both mean "not enough evidence yet", which is a real answer rather than
 *   a missing value. A component that renders null as 0% would tell a student
 *   who has answered two questions that they are failing.
 */

import { apiFetch } from "./api";

export type Mastery = "untouched" | "learning" | "developing" | "solid" | "strong";

export interface TopicProgress {
  topic: string;
  questions: number;
  attempts: number;
  correct: number;
  /** null until there are enough attempts to mean anything. */
  accuracy: number | null;
  mastery: Mastery;
  working_at: string;
  /** null when the evidence does not support a recommendation. */
  suggested: string | null;
  reason: string;
  mistakes: number;
  common_error: string | null;
  last_seen: string | null;
}

export interface ProgressOverview {
  overall: {
    topics_touched: number;
    questions_attempted: number;
    correct: number;
    accuracy: number | null;
    quizzes_taken: number;
    average_quiz_percent: number | null;
    problems_solved: number;
    reviews: number;
  };
  topics: TopicProgress[];
  quiz_trend: {
    id: number;
    title: string;
    percent: number;
    accuracy: number | null;
    mode: string;
    at: string;
  }[];
  errors: { type: string; count: number }[];
  recent: {
    problems_solved: number;
    reviews: number;
    reviews_recent: number;
    window_days: number;
  };
  focus: {
    topic: string;
    difficulty: string;
    accuracy: number | null;
    mastery: Mastery;
    common_error: string | null;
    why: string;
  } | null;
}

export interface NextStep {
  action: "start" | "practise" | "advance";
  topic: string | null;
  difficulty: string;
  message: string;
}

export interface Ladder {
  ladder: string[];
  unranked: string[];
  min_attempts_for_signal: number;
  min_attempts_for_adjustment: number;
  too_easy_above: number;
  too_hard_below: number;
}

export function getProgress(): Promise<ProgressOverview> {
  return apiFetch<ProgressOverview>("/progress");
}

export function getNextStep(): Promise<NextStep> {
  return apiFetch<NextStep>("/progress/next");
}

export function getLadder(): Promise<Ladder> {
  return apiFetch<Ladder>("/progress/ladder");
}

// ── display ───────────────────────────────────────────────────────────────

/**
 * How each mastery band reads.
 *
 * `learning` is deliberately neutral rather than negative: it means "not
 * enough attempts to say", and colouring it as a weakness would tell a
 * student who has just started that they are doing badly.
 */
export const MASTERY_STYLE: Record<
  Mastery,
  { label: string; chip: string; bar: string }
> = {
  untouched: {
    label: "not started",
    chip: "border-line text-muted",
    bar: "bg-line",
  },
  learning: {
    label: "too early to say",
    chip: "border-muted/40 bg-muted/10 text-muted",
    bar: "bg-muted",
  },
  developing: {
    label: "developing",
    chip: "border-wrong/40 bg-wrong/10 text-wrong",
    bar: "bg-wrong",
  },
  solid: {
    label: "solid",
    chip: "border-unverified/40 bg-unverified/10 text-unverified",
    bar: "bg-unverified",
  },
  strong: {
    label: "strong",
    chip: "border-verified/40 bg-verified/10 text-verified",
    bar: "bg-verified",
  },
};
