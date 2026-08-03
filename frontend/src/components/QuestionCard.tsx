/**
 * One practice question, with the answer withheld until the student commits.
 * ═══════════════════════════════════════════════════════════════════════
 *
 * THE INTERACTION IS THE TEACHING DECISION
 *   A question with a visible "show answer" button and nothing else is a
 *   flashcard, and flashcards are where practice goes to die: it is always
 *   easier to read the answer and feel you knew it than to commit to one and
 *   find out you did not.
 *
 *   So the card asks for a commitment first. On multiple choice you pick an
 *   option; on written types you self-mark after revealing. Either way an
 *   attempt is recorded, which is what Phase 8's progress figures are built
 *   from — and what makes "topics I am weak in" mean anything.
 *
 * THE UNVERIFIED BADGE IS NOT DECORATION
 *   A question whose answer key SymPy could not confirm is labelled before
 *   the student spends ten minutes on it. Proofs are legitimately
 *   unverifiable and that is fine; what is not fine is letting a student
 *   mark their own correct work wrong against a key nothing checked.
 */

"use client";

import { useState } from "react";

import { Math, MathText } from "./MathText";
import {
  CHOICE_TYPES,
  humanise,
  type PracticeQuestion,
} from "@/lib/practice";

/** A, B, C, D — option labels, so a student can talk about "option C". */
const LABELS = ["A", "B", "C", "D", "E", "F"];

export function QuestionCard({
  question,
  onReveal,
  onAttempt,
  onBookmark,
}: {
  question: PracticeQuestion;
  /** Fetches the answer. Called when the student chooses to see it. */
  onReveal: (id: number) => Promise<PracticeQuestion>;
  onAttempt: (
    id: number,
    grade: { selected: number[] } | { correct: boolean },
  ) => Promise<PracticeQuestion>;
  onBookmark?: (id: number, bookmarked: boolean) => Promise<void>;
}) {
  const [current, setCurrent] = useState(question);
  const [picked, setPicked] = useState<number[]>([]);
  const [submitted, setSubmitted] = useState(false);
  const [showHint, setShowHint] = useState(false);
  const [saved, setSaved] = useState(question.bookmarked);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState("");

  const isChoice = CHOICE_TYPES.has(current.type);
  const multi = current.type === "multiple_correct";
  const revealed = current.answer != null;

  // Empty until an attempt is recorded — `correct_options` is deliberately
  // withheld before then, so this is ONLY for colouring options afterwards.
  const correctSet = new Set(current.correct_options ?? []);

  // The verdict comes from the server, which is the only side that knows the
  // answer at the moment the student commits.
  const [gotItRight, setGotItRight] = useState<boolean | null>(null);

  function toggle(index: number) {
    if (submitted) return;
    setPicked((prev) =>
      multi
        ? prev.includes(index)
          ? prev.filter((i) => i !== index)
          : [...prev, index]
        : [index],
    );
  }

  /** Record an attempt and pull the graded answer back with it. */
  async function commit(grade: { selected: number[] } | { correct: boolean }) {
    if (current.id == null || busy) return;
    setBusy(true);
    setFailed("");
    try {
      const graded = await onAttempt(current.id, grade);
      setCurrent(graded);
      setGotItRight(graded.was_correct ?? null);
      setSubmitted(true);
    } catch {
      setFailed("Could not record that attempt.");
    } finally {
      setBusy(false);
    }
  }

  /** Reveal without committing — for written types, where the student marks
   *  themselves afterwards. */
  async function reveal() {
    if (current.id == null || busy) return;
    setBusy(true);
    setFailed("");
    try {
      setCurrent(await onReveal(current.id));
    } catch {
      setFailed("Could not load the answer.");
    } finally {
      setBusy(false);
    }
  }

  async function toggleSave() {
    if (current.id == null || !onBookmark) return;
    const next = !saved;
    setSaved(next);
    try {
      await onBookmark(current.id, next);
    } catch {
      setSaved(!next);
    }
  }

  return (
    <article className="overflow-hidden rounded-2xl border border-line bg-slate">
      {/* ── header ──────────────────────────────────────────────────── */}
      <header className="flex flex-wrap items-center gap-2 px-5 py-3.5 sm:px-6">
        <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full border border-line font-mono text-[11px] text-muted">
          {current.number}
        </span>
        <span className="font-mono text-[11px] text-muted">
          {humanise(current.topic)} · {humanise(current.difficulty)} ·{" "}
          {humanise(current.type)} · ~{current.time_minutes} min
        </span>

        {!current.verified && (
          <span
            className="rounded-full border border-unverified/40 bg-unverified/10 px-2 py-0.5 text-[10px] text-unverified"
            title={
              current.verdict_kind === "unverifiable"
                ? "Nothing computable to check — normal for a proof."
                : "The answer key could not be confirmed by the computer algebra system."
            }
          >
            answer key unverified
          </span>
        )}

        {onBookmark && current.id != null && (
          <button
            onClick={() => void toggleSave()}
            aria-pressed={saved}
            title={saved ? "Remove from saved" : "Save this question"}
            className={`ml-auto rounded-lg border px-2.5 py-1 text-[11px] transition-colors ${
              saved
                ? "border-accent bg-accent/10 text-accent"
                : "border-line text-muted hover:border-accent hover:text-accent"
            }`}
          >
            {saved ? "★" : "☆"}
          </button>
        )}
      </header>

      {/* ── the question ────────────────────────────────────────────── */}
      <div className="border-t border-line px-5 py-4 sm:px-6">
        <MathText className="text-[15px] leading-relaxed">{current.prompt}</MathText>

        {isChoice && current.options.length > 0 && (
          <ul className="mt-4 space-y-2">
            {current.options.map((option, index) => {
              const chosen = picked.includes(index);
              const isCorrect = correctSet.has(index);

              // Colour only after submitting. Before that, marking anything
              // would give the answer away.
              let tone = "border-line hover:border-accent/60";
              if (submitted && isCorrect) tone = "border-verified/60 bg-verified/10";
              else if (submitted && chosen) tone = "border-wrong/60 bg-wrong/10";
              else if (chosen) tone = "border-accent bg-accent/10";

              return (
                <li key={index}>
                  <button
                    onClick={() => toggle(index)}
                    disabled={submitted}
                    className={`flex w-full items-start gap-3 rounded-xl border px-3.5 py-2.5 text-left transition-colors ${tone}`}
                  >
                    <span className="mt-0.5 font-mono text-[11px] text-muted">
                      {LABELS[index] ?? index + 1}
                    </span>
                    <span className="min-w-0 flex-1 overflow-x-auto">
                      <Math latex={option} />
                    </span>
                    {submitted && isCorrect && (
                      <span className="text-[11px] text-verified">correct</span>
                    )}
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </div>

      {/* ── hint ────────────────────────────────────────────────────── */}
      {current.hint && !revealed && (
        <div className="border-t border-line px-5 py-3 sm:px-6">
          {showHint ? (
            <p className="text-[13px] text-muted">
              <MathText>{current.hint}</MathText>
            </p>
          ) : (
            <button
              onClick={() => setShowHint(true)}
              className="font-mono text-[11px] tracking-[0.14em] text-muted uppercase hover:text-paper"
            >
              Show hint
            </button>
          )}
        </div>
      )}

      {/* ── the answer ──────────────────────────────────────────────── */}
      {revealed && (
        <div className="border-t border-line px-5 py-4 sm:px-6">
          {submitted && isChoice && gotItRight != null && (
            <p
              className={`mb-3 text-sm font-medium ${
                gotItRight ? "text-verified" : "text-wrong"
              }`}
            >
              {gotItRight ? "Correct." : "Not quite."}
            </p>
          )}

          <h3 className="mb-2 font-mono text-[11px] tracking-[0.16em] text-muted uppercase">
            Answer
          </h3>
          {current.answer_latex ? (
            <Math latex={current.answer_latex} display className="text-lg" />
          ) : (
            <MathText className="text-[15px]">{current.answer ?? ""}</MathText>
          )}

          {current.solution_outline && current.solution_outline.length > 0 && (
            <>
              <h3 className="mt-4 mb-2 font-mono text-[11px] tracking-[0.16em] text-muted uppercase">
                Method
              </h3>
              <ol className="space-y-1.5">
                {current.solution_outline.map((line, i) => (
                  <li key={i} className="flex gap-2.5 text-[14px] text-muted">
                    <span className="font-mono text-[11px]">{i + 1}</span>
                    <MathText>{line}</MathText>
                  </li>
                ))}
              </ol>
            </>
          )}
        </div>
      )}

      {/* ── actions ─────────────────────────────────────────────────── */}
      <div className="flex flex-wrap items-center gap-2 border-t border-line px-5 py-3 sm:px-6">
        {isChoice ? (
          !submitted ? (
            <button
              onClick={() => void commit({ selected: picked })}
              disabled={picked.length === 0 || busy}
              className="rounded-xl bg-accent px-4 py-2 text-[13px] font-semibold text-paper transition-colors hover:bg-accent-soft disabled:opacity-40"
            >
              {busy ? "Checking…" : "Check answer"}
            </button>
          ) : (
            <span className="font-mono text-[11px] text-muted">
              attempt {current.attempts} · {current.correct} correct
            </span>
          )
        ) : !revealed ? (
          <button
            onClick={() => void reveal()}
            disabled={busy}
            className="rounded-xl border border-line px-4 py-2 text-[13px] text-muted transition-colors hover:border-accent hover:text-accent disabled:opacity-40"
          >
            {busy ? "Loading…" : "Show answer"}
          </button>
        ) : !submitted ? (
          <>
            {/* Written types cannot be auto-marked, so the student marks
                themselves. An honest self-mark is worth more than a
                string comparison that fails on "2x" vs "2 x". */}
            <span className="text-[13px] text-muted">Did you get it right?</span>
            <button
              onClick={() => void commit({ correct: true })}
              disabled={busy}
              className="rounded-lg border border-verified/50 px-3 py-1.5 text-[12px] text-verified transition-colors hover:bg-verified/10 disabled:opacity-40"
            >
              Yes
            </button>
            <button
              onClick={() => void commit({ correct: false })}
              disabled={busy}
              className="rounded-lg border border-wrong/50 px-3 py-1.5 text-[12px] text-wrong transition-colors hover:bg-wrong/10 disabled:opacity-40"
            >
              No
            </button>
          </>
        ) : (
          <span className="font-mono text-[11px] text-muted">
            attempt {current.attempts} · {current.correct} correct
          </span>
        )}

        {failed && <span className="text-[11px] text-wrong">{failed}</span>}
      </div>
    </article>
  );
}
