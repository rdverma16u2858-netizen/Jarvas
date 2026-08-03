/**
 * Progress — what the last four phases have been quietly recording.
 * ═══════════════════════════════════════════════════════════════════════
 *
 * WHAT LEADS
 *   "What to do next" sits above everything. A progress page that opens with
 *   ten topics and no priority hands the student back the exact question they
 *   came here to have answered.
 *
 * WHY "TOO EARLY TO SAY" IS STYLED NEUTRALLY
 *   A topic with three attempts is not a weakness — it is a topic with three
 *   attempts. Colouring it red would tell someone who started yesterday that
 *   they are failing, which is both false and the fastest way to make them
 *   stop.
 *
 * WHY THE CHARTS ARE CSS
 *   A quiz trend of twenty points and an error breakdown of eight bars do not
 *   need a charting library. Div widths render identically, ship nothing, and
 *   inherit the theme for free.
 */

"use client";

import Link from "next/link";
import { PageNav } from "@/components/PageNav";
import { useEffect, useState } from "react";

import { ERROR_LABEL, type ErrorType } from "@/lib/review";
import { humanise } from "@/lib/practice";
import {
  MASTERY_STYLE,
  getLadder,
  getNextStep,
  getProgress,
  type Ladder,
  type NextStep,
  type ProgressOverview,
  type TopicProgress,
} from "@/lib/progress";

export default function ProgressPage() {
  const [data, setData] = useState<ProgressOverview | null>(null);
  const [next, setNext] = useState<NextStep | null>(null);
  const [ladder, setLadder] = useState<Ladder | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    Promise.all([getProgress(), getNextStep(), getLadder()])
      .then(([overview, step, rungs]) => {
        setData(overview);
        setNext(step);
        setLadder(rungs);
      })
      .catch(() => setError("Cannot reach the backend."));
  }, []);

  const started = (data?.overall.questions_attempted ?? 0) > 0;

  return (
    <main className="mx-auto flex min-h-dvh max-w-3xl flex-col px-4 sm:px-6">
      <header className="pt-10 pb-6">
        <PageNav />
        <div className="flex items-center gap-3">
          <h1 className="font-display text-3xl font-bold tracking-tight">Progress</h1>
        </div>
        <p className="mt-1.5 text-sm leading-relaxed text-muted">
          Built from what you have actually done — practice attempts, marked
          papers and the mistakes found in your working.
        </p>
      </header>

      {error && (
        <div className="rounded-xl border border-wrong/40 bg-wrong/5 p-4">
          <p className="text-sm text-wrong">{error}</p>
        </div>
      )}

      {data && (
        <div className="flex-1 space-y-4 pb-10">
          {/* ── what to do next ──────────────────────────────────────── */}
          {next && (
            <section className="rounded-2xl border border-accent/40 bg-accent/5 p-5 sm:p-6">
              <h2 className="mb-2 font-mono text-[11px] tracking-[0.16em] text-accent uppercase">
                What to do next
              </h2>
              <p className="text-[15px] leading-relaxed">{next.message}</p>

              {next.action !== "start" && next.topic && (
                // The recommendation is carried through as query parameters,
                // so the practice page opens already set to it. A button that
                // names a topic and level and then drops you on a form
                // defaulted to something else is advice you have to re-enter
                // by hand.
                <Link
                  href={`/practice?topic=${encodeURIComponent(next.topic)}&difficulty=${encodeURIComponent(next.difficulty)}`}
                  className="mt-3 inline-block rounded-xl bg-accent px-4 py-2 text-[13px] font-semibold text-paper transition-colors hover:bg-accent-soft"
                >
                  Practise {humanise(next.topic)} at {humanise(next.difficulty)}
                </Link>
              )}
              {next.action === "start" && (
                <Link
                  href="/practice"
                  className="mt-3 inline-block rounded-xl bg-accent px-4 py-2 text-[13px] font-semibold text-paper transition-colors hover:bg-accent-soft"
                >
                  Generate some questions
                </Link>
              )}
            </section>
          )}

          {/* ── the totals ───────────────────────────────────────────── */}
          <section className="rounded-2xl border border-line bg-slate p-5 sm:p-6">
            <div className="flex flex-wrap gap-x-8 gap-y-3">
              <Stat
                label="questions attempted"
                value={String(data.overall.questions_attempted)}
              />
              <Stat
                label="accuracy"
                value={
                  data.overall.accuracy === null
                    ? "—"
                    : `${(data.overall.accuracy * 100).toFixed(0)}%`
                }
              />
              <Stat label="topics touched" value={String(data.overall.topics_touched)} />
              <Stat label="papers taken" value={String(data.overall.quizzes_taken)} />
              <Stat
                label="paper average"
                value={
                  data.overall.average_quiz_percent === null
                    ? "—"
                    : `${data.overall.average_quiz_percent}%`
                }
              />
              <Stat
                label="problems solved"
                value={String(data.overall.problems_solved)}
              />
            </div>
          </section>

          {!started && (
            <p className="rounded-2xl border border-dashed border-line p-6 text-center text-sm text-muted">
              Nothing recorded yet. Answer a few practice questions and this page
              fills in.
            </p>
          )}

          {/* ── per topic ────────────────────────────────────────────── */}
          {data.topics.length > 0 && (
            <section className="overflow-hidden rounded-2xl border border-line bg-slate">
              <h2 className="px-5 py-3.5 font-mono text-[11px] tracking-[0.16em] text-muted uppercase sm:px-6">
                By topic
              </h2>
              <ul>
                {data.topics.map((topic) => (
                  <TopicRow key={topic.topic} topic={topic} />
                ))}
              </ul>
            </section>
          )}

          {/* ── paper scores over time ───────────────────────────────── */}
          {data.quiz_trend.length > 0 && (
            <section className="rounded-2xl border border-line bg-slate p-5 sm:p-6">
              <h2 className="mb-4 font-mono text-[11px] tracking-[0.16em] text-muted uppercase">
                Paper scores
              </h2>
              <div className="flex h-32 items-end gap-1.5">
                {data.quiz_trend.map((point) => (
                  <div
                    key={point.id}
                    className="group relative flex flex-1 flex-col justify-end"
                    title={`${point.title} — ${point.percent}%`}
                  >
                    <div
                      className={`w-full rounded-t ${
                        point.percent >= 75
                          ? "bg-verified"
                          : point.percent >= 40
                            ? "bg-unverified"
                            : "bg-wrong"
                      }`}
                      // Floored so a zero-score paper is still a visible mark
                      // rather than an apparent gap in the record.
                      style={{ height: `${Math.max(4, point.percent)}%` }}
                    />
                  </div>
                ))}
              </div>
              <p className="mt-2 font-mono text-[10px] text-muted">
                oldest → newest · {data.quiz_trend.length} papers
              </p>
            </section>
          )}

          {/* ── mistakes ─────────────────────────────────────────────── */}
          {data.errors.length > 0 && (
            <section className="rounded-2xl border border-line bg-slate p-5 sm:p-6">
              <h2 className="mb-4 font-mono text-[11px] tracking-[0.16em] text-muted uppercase">
                Where your mistakes are
              </h2>
              <ul className="space-y-2.5">
                {data.errors.map((entry) => {
                  const top = data.errors[0].count || 1;
                  return (
                    <li key={entry.type} className="flex items-center gap-3">
                      <span className="w-40 shrink-0 text-[13px] text-muted">
                        {ERROR_LABEL[entry.type as ErrorType] ?? humanise(entry.type)}
                      </span>
                      <span className="h-2 flex-1 overflow-hidden rounded-full bg-ink">
                        <span
                          className="block h-full rounded-full bg-wrong"
                          style={{ width: `${(entry.count / top) * 100}%` }}
                        />
                      </span>
                      <span className="w-6 text-right font-mono text-[12px] text-muted">
                        {entry.count}
                      </span>
                    </li>
                  );
                })}
              </ul>
            </section>
          )}

          {/* ── the ladder this page reasons with ────────────────────── */}
          {ladder && (
            <details className="rounded-2xl border border-line bg-slate px-5 py-3.5 sm:px-6">
              <summary className="cursor-pointer font-mono text-[11px] tracking-[0.16em] text-muted uppercase hover:text-paper">
                How levels are ordered
              </summary>
              <p className="mt-3 font-mono text-[12px] leading-relaxed text-muted">
                {ladder.ladder.map(humanise).join("  →  ")}
              </p>
              <p className="mt-3 text-[13px] leading-relaxed text-muted">
                {humanise(ladder.unranked.join(", "))} sits outside this order — it
                is a different syllabus rather than a harder one, so nothing moves
                into or out of it.
              </p>
              <p className="mt-2 text-[13px] leading-relaxed text-muted">
                A level change is suggested above{" "}
                {(ladder.too_easy_above * 100).toFixed(0)}% or below{" "}
                {(ladder.too_hard_below * 100).toFixed(0)}%, and only after{" "}
                {ladder.min_attempts_for_adjustment} attempts on a topic.
              </p>
            </details>
          )}
        </div>
      )}
    </main>
  );
}

// ── pieces ────────────────────────────────────────────────────────────────

function TopicRow({ topic }: { topic: TopicProgress }) {
  const style = MASTERY_STYLE[topic.mastery];
  const percent = topic.accuracy === null ? null : topic.accuracy * 100;

  return (
    <li className="border-t border-line px-5 py-4 sm:px-6">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-[15px]">{humanise(topic.topic)}</span>
        <span
          className={`rounded-full border px-2 py-0.5 text-[10px] ${style.chip}`}
        >
          {style.label}
        </span>
        <span className="rounded-full border border-line px-2 py-0.5 font-mono text-[10px] text-muted">
          {humanise(topic.working_at)}
        </span>
        <span className="ml-auto font-mono text-[11px] text-muted">
          {topic.correct}/{topic.attempts}
          {percent !== null && ` · ${percent.toFixed(0)}%`}
        </span>
      </div>

      {/* The bar is the accuracy; its colour is the mastery band. A topic with
          too few attempts to judge shows no bar at all rather than a
          misleadingly short one. */}
      {percent !== null ? (
        <span className="mt-2 block h-1.5 overflow-hidden rounded-full bg-ink">
          <span
            className={`block h-full rounded-full ${style.bar}`}
            style={{ width: `${percent}%` }}
          />
        </span>
      ) : (
        <span className="mt-2 block h-1.5 rounded-full bg-ink" />
      )}

      {/* The working level is the chip above; the reason states it too, so it
          is not repeated in prose here. */}
      <p className="mt-2 text-[12px] leading-relaxed text-muted">{topic.reason}</p>

      {topic.common_error && (
        <p className="mt-1 text-[12px] text-muted">
          Most of your mistakes here are{" "}
          <span className="text-paper">
            {ERROR_LABEL[topic.common_error as ErrorType] ??
              humanise(topic.common_error)}
          </span>{" "}
          ({topic.mistakes} recorded).
        </p>
      )}
    </li>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <span className="block font-mono text-2xl tabular-nums text-paper">{value}</span>
      <span className="font-mono text-[10px] tracking-[0.14em] text-muted uppercase">
        {label}
      </span>
    </div>
  );
}
