/**
 * Quizzes and mock tests.
 * ═══════════════════════════════════════════════════════════════════════
 *
 * THREE STATES, ONE PAGE
 *   setup    choose the paper
 *   running  answer it, against a clock
 *   result   the marked paper with the answer key
 *
 *   They share a page because they are one continuous act. Routing between
 *   them would put a navigation — and a chance to lose an in-progress paper —
 *   in the middle of a timed test.
 *
 * THE COUNTDOWN IS A DISPLAY, NOT THE TRUTH
 *   `seconds_remaining` comes from the server on every response. The local
 *   interval below ticks it down so the number moves once a second, and every
 *   answer resyncs it. If the two ever disagree, the server wins — a laptop
 *   that slept must not gain the time it was asleep for.
 *
 *   At zero the paper submits itself. Leaving a student staring at 0:00 with
 *   an active Submit button would be a worse lie than the timer running out.
 *
 * WHY ANSWERS POST IMMEDIATELY RATHER THAN AT SUBMIT
 *   A mock test can run an hour. Holding every answer in React state until
 *   the end means a refresh, a crash or a closed tab loses all of it. Each
 *   answer is written as it is made, so the paper survives.
 */

"use client";

import Link from "next/link";
import { PageNav } from "@/components/PageNav";
import { useCallback, useEffect, useRef, useState } from "react";

import { Math, MathText } from "@/components/MathText";
import { ApiError, NetworkError } from "@/lib/api";
import { printElement } from "@/lib/export";
import { humanise } from "@/lib/practice";
import {
  CLOCK_WARNING_SECONDS,
  answerQuestion,
  createQuiz,
  formatClock,
  getAvailability,
  getQuiz,
  getQuizStats,
  listQuizzes,
  submitQuiz,
  type Quiz,
  type QuizMode,
  type QuizQuestion,
  type QuizStats,
} from "@/lib/quiz";
import { getVocabulary, type Vocabulary } from "@/lib/practice";

const LABELS = ["A", "B", "C", "D", "E", "F"];

export default function QuizPage() {
  const [vocabulary, setVocabulary] = useState<Vocabulary | null>(null);
  const [mode, setMode] = useState<QuizMode>("practice");
  const [topic, setTopic] = useState("");
  const [difficulty, setDifficulty] = useState("");
  const [count, setCount] = useState(5);
  const [available, setAvailable] = useState<number | null>(null);

  const [quiz, setQuiz] = useState<Quiz | null>(null);
  const [remaining, setRemaining] = useState<number | null>(null);
  const [stats, setStats] = useState<QuizStats | null>(null);
  const [unfinished, setUnfinished] = useState<Quiz | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  // Guards the auto-submit so a tick and a click cannot both fire it.
  const submittingRef = useRef(false);
  //: The marked paper, for print-to-PDF.
  const paperRef = useRef<HTMLDivElement>(null);

  const running = quiz?.status === "in_progress";
  const marked = quiz?.status === "submitted";

  useEffect(() => {
    getVocabulary().then(setVocabulary).catch(() => {});
    getQuizStats().then(setStats).catch(() => {});

    // Answers are written as they are made, so a refreshed or reopened paper
    // still exists on the server. Without this the page could not get back to
    // it, and "your answers are saved" would be true but useless.
    listQuizzes()
      .then((all) => setUnfinished(all.find((q) => q.status === "in_progress") ?? null))
      .catch(() => {});
  }, []);

  const resume = useCallback(async (id: number) => {
    setBusy(true);
    try {
      const paper = await getQuiz(id);
      // It may have expired while the page was away, in which case the server
      // has already said so and there is nothing to resume.
      if (paper.status === "in_progress") {
        setQuiz(paper);
        setRemaining(paper.seconds_remaining);
      } else {
        setError("That paper's time ran out while you were away.");
      }
      setUnfinished(null);
    } catch {
      setError("Could not reopen that paper.");
    } finally {
      setBusy(false);
    }
  }, []);

  // How many questions the bank could actually supply, so the count field is
  // an informed choice rather than a guess that 409s.
  useEffect(() => {
    getAvailability({ topic: topic || undefined, difficulty: difficulty || undefined })
      .then((a) => setAvailable(a.available))
      .catch(() => setAvailable(null));
  }, [topic, difficulty]);

  const submit = useCallback(async () => {
    if (!quiz || submittingRef.current) return;
    submittingRef.current = true;
    setBusy(true);
    try {
      setQuiz(await submitQuiz(quiz.id));
      setRemaining(null);
      getQuizStats().then(setStats).catch(() => {});
    } catch (caught) {
      setError(
        caught instanceof ApiError ? caught.message : "Could not submit the paper.",
      );
    } finally {
      setBusy(false);
      submittingRef.current = false;
    }
  }, [quiz]);

  // The local tick. Resynced from the server on every answer.
  useEffect(() => {
    if (!running || remaining === null) return;

    if (remaining <= 0) {
      void submit();
      return;
    }

    const timer = setTimeout(() => setRemaining((r) => (r === null ? null : r - 1)), 1000);
    return () => clearTimeout(timer);
  }, [running, remaining, submit]);

  const start = useCallback(async () => {
    setBusy(true);
    setError("");
    try {
      const created = await createQuiz({
        count,
        mode,
        topic: topic || undefined,
        difficulty: difficulty || undefined,
      });
      setQuiz(created);
      setRemaining(created.seconds_remaining);
    } catch (caught) {
      if (caught instanceof NetworkError) {
        setError("Cannot reach the backend.");
      } else if (caught instanceof ApiError) {
        setError(caught.message);
      } else {
        setError(String(caught));
      }
    } finally {
      setBusy(false);
    }
  }, [count, mode, topic, difficulty]);

  const answer = useCallback(
    async (question: QuizQuestion, selected: number[]) => {
      if (!quiz || !running) return;

      // Optimistic, so selecting an option feels immediate on a long paper.
      setQuiz((q) =>
        q
          ? {
              ...q,
              questions: q.questions.map((x) =>
                x.id === question.id ? { ...x, selected } : x,
              ),
            }
          : q,
      );

      try {
        const synced = await answerQuestion(quiz.id, question.id, { selected });
        setQuiz(synced);
        // The server's clock wins over the local tick.
        setRemaining(synced.seconds_remaining);
      } catch (caught) {
        if (caught instanceof ApiError && caught.status === 409) {
          // Time is up, or the paper closed under us. Reload the truth.
          setError(caught.message);
          void submit();
        }
      }
    },
    [quiz, running, submit],
  );

  function toggle(question: QuizQuestion, index: number) {
    const multi = question.type === "multiple_correct";
    const current = question.selected ?? [];
    const next = multi
      ? current.includes(index)
        ? current.filter((i) => i !== index)
        : [...current, index]
      : [index];
    void answer(question, next);
  }

  const answeredCount = quiz?.questions.filter((q) => q.selected.length > 0).length ?? 0;

  return (
    <div>
      {/* ── the clock, pinned while a timed paper runs ─────────────── */}
      {running && remaining !== null && (
        <div className="sticky top-0 z-20 border-b border-line bg-ink/95 backdrop-blur">
          <div className="mx-auto flex max-w-3xl items-center gap-3 px-4 py-2.5 sm:px-6">
            <span
              className={`font-mono text-lg tabular-nums ${
                remaining <= CLOCK_WARNING_SECONDS ? "text-wrong" : "text-paper"
              }`}
              aria-live="off"
            >
              {formatClock(remaining)}
            </span>
            <span className="font-mono text-[11px] text-muted">
              {answeredCount}/{quiz.question_count} answered
            </span>
            <button
              onClick={() => void submit()}
              disabled={busy}
              className="ml-auto rounded-lg bg-accent px-4 py-1.5 text-[12px] font-semibold text-paper transition-colors hover:bg-accent-soft disabled:opacity-40"
            >
              {busy ? "Marking…" : "Submit"}
            </button>
          </div>
        </div>
      )}

      <main className="mx-auto flex min-h-dvh max-w-3xl flex-col px-4 sm:px-6">
        <header className="pt-10 pb-6">
        <PageNav />
          <div className="flex items-center gap-3">
            <h1 className="font-display text-3xl font-bold tracking-tight">
              {quiz ? quiz.title : "Quiz"}
            </h1>
          </div>
          {!quiz && (
            <p className="mt-1.5 text-sm leading-relaxed text-muted">
              Drawn from questions whose answer keys the computer algebra system
              confirmed. A mock test is timed and uses JEE marking — four for a
              correct answer, minus one for a wrong one.
            </p>
          )}
        </header>

        {error && (
          <div className="mb-4 rounded-xl border border-wrong/40 bg-wrong/5 p-4">
            <p className="text-sm text-wrong">{error}</p>
          </div>
        )}

        {/* ── setup ──────────────────────────────────────────────────── */}
        {!quiz && (
          <>
            {unfinished && (
              <div className="mb-4 flex flex-wrap items-center gap-3 rounded-xl border border-accent/40 bg-accent/5 p-4">
                <div className="min-w-0 flex-1">
                  <p className="text-sm text-paper">{unfinished.title}</p>
                  <p className="mt-0.5 font-mono text-[11px] text-muted">
                    still open · {unfinished.question_count} questions
                    {unfinished.seconds_remaining !== null &&
                      ` · ${formatClock(unfinished.seconds_remaining)} left`}
                  </p>
                </div>
                <button
                  onClick={() => void resume(unfinished.id)}
                  disabled={busy}
                  className="rounded-lg bg-accent px-4 py-2 text-[12px] font-semibold text-paper transition-colors hover:bg-accent-soft disabled:opacity-40"
                >
                  Resume
                </button>
                <button
                  onClick={() => setUnfinished(null)}
                  className="rounded-lg border border-line px-3 py-2 text-[12px] text-muted transition-colors hover:text-paper"
                >
                  Dismiss
                </button>
              </div>
            )}

            <section className="rounded-2xl border border-line bg-slate p-4 sm:p-5">
              <div className="mb-3 flex gap-1.5">
                {(["practice", "mock_test"] as QuizMode[]).map((option) => (
                  <button
                    key={option}
                    onClick={() => setMode(option)}
                    className={`rounded-full border px-3.5 py-1.5 text-[12px] transition-colors ${
                      mode === option
                        ? "border-accent bg-accent/10 text-accent"
                        : "border-line text-muted hover:text-paper"
                    }`}
                  >
                    {option === "practice" ? "Practice" : "Mock test"}
                  </button>
                ))}
                <span className="self-center pl-1 font-mono text-[10px] text-muted/70">
                  {mode === "practice"
                    ? "untimed · +1 per correct"
                    : "timed · +4 correct, −1 wrong"}
                </span>
              </div>

              <div className="grid gap-3 sm:grid-cols-3">
                <Field label="Topic">
                  <Select
                    value={topic}
                    onChange={setTopic}
                    options={vocabulary?.topics ?? []}
                    anyLabel="any topic"
                  />
                </Field>
                <Field label="Difficulty">
                  <Select
                    value={difficulty}
                    onChange={setDifficulty}
                    options={vocabulary?.difficulties ?? []}
                    anyLabel="any level"
                  />
                </Field>
                <Field
                  label={
                    available === null ? "Questions" : `Questions (${available} available)`
                  }
                >
                  <input
                    type="number"
                    min={1}
                    max={60}
                    value={count}
                    onChange={(e) => setCount(Number(e.target.value) || 1)}
                    className="w-full rounded-lg border border-line bg-ink px-3 py-2 text-[13px] text-paper focus:border-accent focus:outline-none"
                  />
                </Field>
              </div>

              <button
                onClick={() => void start()}
                disabled={busy || (available !== null && available < count)}
                className="mt-4 rounded-xl bg-accent px-5 py-2.5 text-sm font-semibold text-paper transition-colors hover:bg-accent-soft disabled:opacity-40"
              >
                {busy ? "Assembling…" : mode === "mock_test" ? "Start the test" : "Start"}
              </button>

              {available !== null && available < count && (
                <p className="mt-2.5 text-[12px] text-muted">
                  Only {available} verified {available === 1 ? "question" : "questions"}{" "}
                  match. <Link href="/practice" className="text-accent underline">
                    Generate more
                  </Link>{" "}
                  first.
                </p>
              )}
            </section>

            {stats && stats.quizzes > 0 && (
              <section className="mt-4 rounded-2xl border border-line bg-slate p-4 sm:p-5">
                <h2 className="mb-3 font-mono text-[11px] tracking-[0.16em] text-muted uppercase">
                  Your papers
                </h2>
                <div className="flex flex-wrap gap-x-8 gap-y-2 text-sm">
                  <Stat label="taken" value={String(stats.quizzes)} />
                  <Stat label="average" value={`${stats.average_percent}%`} />
                  <Stat label="best" value={`${stats.best_percent}%`} />
                </div>
              </section>
            )}
          </>
        )}

        {/* ── the paper ──────────────────────────────────────────────── */}
        {quiz && (
          <div ref={paperRef} className="flex-1 space-y-4 pb-8">
            {marked && (
              <ResultSummary quiz={quiz} onPrint={() => printElement(paperRef.current)} />
            )}

            {quiz.questions.map((question) => (
              <article
                key={question.id}
                className="overflow-hidden rounded-2xl border border-line bg-slate"
              >
                <header className="flex flex-wrap items-center gap-2 px-5 py-3 sm:px-6">
                  <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full border border-line font-mono text-[11px] text-muted">
                    {question.position + 1}
                  </span>
                  <span className="font-mono text-[11px] text-muted">
                    {humanise(question.topic)} · {humanise(question.difficulty)}
                  </span>
                  {marked && question.marks !== null && (
                    <span
                      className={`ml-auto font-mono text-[12px] ${
                        question.is_correct === true
                          ? "text-verified"
                          : question.is_correct === false
                            ? "text-wrong"
                            : "text-muted"
                      }`}
                    >
                      {question.marks > 0 ? `+${question.marks}` : question.marks}
                    </span>
                  )}
                </header>

                <div className="border-t border-line px-5 py-4 sm:px-6">
                  <MathText className="text-[15px] leading-relaxed">
                    {question.prompt}
                  </MathText>

                  <ul className="mt-4 space-y-2">
                    {question.options.map((option, index) => {
                      const chosen = question.selected.includes(index);
                      const isKey = question.correct_options?.includes(index) ?? false;

                      let tone = "border-line hover:border-accent/60";
                      if (marked && isKey) tone = "border-verified/60 bg-verified/10";
                      else if (marked && chosen) tone = "border-wrong/60 bg-wrong/10";
                      else if (chosen) tone = "border-accent bg-accent/10";

                      return (
                        <li key={index}>
                          <button
                            onClick={() => toggle(question, index)}
                            disabled={!running}
                            className={`flex w-full items-start gap-3 rounded-xl border px-3.5 py-2.5 text-left transition-colors ${tone}`}
                          >
                            <span className="mt-0.5 font-mono text-[11px] text-muted">
                              {LABELS[index] ?? index + 1}
                            </span>
                            <span className="min-w-0 flex-1 overflow-x-auto">
                              <Math latex={option} />
                            </span>
                            {marked && isKey && (
                              <span className="text-[11px] text-verified">correct</span>
                            )}
                          </button>
                        </li>
                      );
                    })}
                  </ul>

                  {marked && question.solution_outline && (
                    <div className="mt-4 border-t border-line pt-3">
                      <h3 className="mb-2 font-mono text-[10px] tracking-[0.14em] text-muted uppercase">
                        Method
                      </h3>
                      <ol className="space-y-1.5">
                        {question.solution_outline.map((line, i) => (
                          <li key={i} className="flex gap-2.5 text-[13px] text-muted">
                            <span className="font-mono text-[11px]">{i + 1}</span>
                            <MathText>{line}</MathText>
                          </li>
                        ))}
                      </ol>
                    </div>
                  )}
                </div>
              </article>
            ))}

            {running && !quiz.time_limit_seconds && (
              <button
                onClick={() => void submit()}
                disabled={busy}
                className="w-full rounded-xl bg-accent px-5 py-3 text-sm font-semibold text-paper transition-colors hover:bg-accent-soft disabled:opacity-40"
              >
                {busy
                  ? "Marking…"
                  : `Submit — ${answeredCount} of ${quiz.question_count} answered`}
              </button>
            )}

            {marked && (
              <button
                onClick={() => {
                  setQuiz(null);
                  setRemaining(null);
                  setError("");
                }}
                className="w-full rounded-xl border border-line px-5 py-3 text-sm text-muted transition-colors hover:border-accent hover:text-accent"
              >
                New paper
              </button>
            )}
          </div>
        )}
      </main>
    </div>
  );
}

// ── pieces ────────────────────────────────────────────────────────────────

function ResultSummary({
  quiz,
  onPrint,
}: {
  quiz: Quiz;
  onPrint: () => void;
}) {
  const percent = quiz.percent ?? 0;
  const tone =
    percent >= 75 ? "text-verified" : percent >= 40 ? "text-unverified" : "text-wrong";

  return (
    <section className="rounded-2xl border border-line bg-slate p-5 sm:p-6">
      <div className="flex flex-wrap items-baseline gap-3">
        <span className={`font-display text-4xl font-bold ${tone}`}>
          {quiz.score}
          <span className="text-xl text-muted">/{quiz.max_score}</span>
        </span>
        <span className={`font-mono text-lg ${tone}`}>{percent}%</span>
        {quiz.mode === "mock_test" && (
          <span className="font-mono text-[11px] text-muted">
            JEE marking · +{quiz.marks_correct} / {quiz.marks_wrong}
          </span>
        )}
      </div>

      <div className="mt-4 flex flex-wrap gap-x-8 gap-y-2 text-sm">
        <Stat label="correct" value={String(quiz.correct_count)} tone="text-verified" />
        <Stat label="wrong" value={String(quiz.wrong_count)} tone="text-wrong" />
        <Stat label="left blank" value={String(quiz.unattempted_count)} />
        {/* Accuracy answers a different question from the score: of what you
            attempted, how much did you get right. */}
        {/* toFixed rather than Math.round: the `Math` imported from MathText
            is a component and shadows the global here. */}
        <Stat
          label="accuracy on attempted"
          value={quiz.accuracy === null ? "—" : `${(quiz.accuracy * 100).toFixed(0)}%`}
        />
        <Stat label="time taken" value={formatClock(quiz.elapsed_seconds)} />
      </div>

      {/* A marked paper is worth keeping — the whole point of a mock test is
          comparing it against the next one. */}
      <button
        onClick={onPrint}
        title="Opens your print dialogue — choose Save as PDF"
        className="no-print mt-4 rounded-lg border border-line px-3 py-1.5 text-[11px] text-muted transition-colors hover:border-accent hover:text-accent"
      >
        Save this paper as PDF
      </button>
    </section>
  );
}

function Stat({
  label,
  value,
  tone = "text-paper",
}: {
  label: string;
  value: string;
  tone?: string;
}) {
  return (
    <div>
      <span className={`block font-mono text-lg tabular-nums ${tone}`}>{value}</span>
      <span className="font-mono text-[10px] tracking-[0.14em] text-muted uppercase">
        {label}
      </span>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1.5 block font-mono text-[10px] tracking-[0.14em] text-muted uppercase">
        {label}
      </span>
      {children}
    </label>
  );
}

function Select({
  value,
  onChange,
  options,
  anyLabel,
}: {
  value: string;
  onChange: (value: string) => void;
  options: string[];
  anyLabel: string;
}) {
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className="w-full rounded-lg border border-line bg-ink px-3 py-2 text-[13px] text-paper focus:border-accent focus:outline-none"
    >
      <option value="">{anyLabel}</option>
      {options.map((option) => (
        <option key={option} value={option}>
          {humanise(option)}
        </option>
      ))}
    </select>
  );
}
