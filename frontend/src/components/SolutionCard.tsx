/**
 * Renders a full ten-part solution.
 * ═══════════════════════════════════════════════════════════════════════
 *
 * ORDER IS A TEACHING DECISION, NOT A LAYOUT ONE
 *   The verdict comes first, then the answer, then the steps. A student who
 *   only reads the top of the card should still learn the two things that
 *   matter most: what the answer is, and whether it was checked.
 *
 *   The teaching material (mistakes, alternative method, practice) sits below
 *   the derivation, where someone who has followed the working will reach it.
 *
 * THE VERDICT IS NEVER DECORATIVE
 *   A refuted answer is shown with the correct value SymPy computed, in red,
 *   above the solution — not hidden, and not quietly styled the same as a
 *   verified one. Presenting an unverified answer as if it were checked is
 *   the single worst thing this product could do.
 */

"use client";

import { useEffect, useRef, useState } from "react";

import { CopyButton } from "./CopyButton";
import { Math, MathText } from "./MathText";
import { answerAsLatex, printElement, solutionAsMarkdown } from "@/lib/export";
import type { Solution, Verdict } from "@/lib/solve";

const VERDICT_STYLE: Record<
  Verdict["kind"],
  { label: string; chip: string; dot: string; blurb: string }
> = {
  verified: {
    label: "Verified",
    chip: "border-verified/40 bg-verified/10 text-verified",
    dot: "bg-verified",
    blurb: "Recomputed independently by a computer algebra system.",
  },
  refuted: {
    label: "Failed verification",
    chip: "border-wrong/40 bg-wrong/10 text-wrong",
    dot: "bg-wrong",
    blurb: "The check disagreed with this answer. Trust the computed value below.",
  },
  unverifiable: {
    label: "Not checkable",
    chip: "border-unverified/40 bg-unverified/10 text-unverified",
    dot: "bg-unverified",
    blurb: "A proof or descriptive answer — there is no single value to recompute.",
  },
  error: {
    label: "Check failed to run",
    chip: "border-muted/40 bg-muted/10 text-muted",
    dot: "bg-muted",
    blurb: "The verifier could not evaluate this. The answer is unconfirmed.",
  },
};

function Section({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section className="border-t border-line px-5 py-4 sm:px-6">
      <h3 className="mb-3 font-mono text-[11px] uppercase tracking-[0.16em] text-muted">
        {title}
      </h3>
      {children}
    </section>
  );
}

/**
 * Save / unsave, with the note editor it reveals.
 *
 * The button updates optimistically and rolls back if the request fails.
 * A bookmark is a low-stakes toggle pressed in passing; waiting on a round
 * trip before the star fills makes it feel broken, and a silent failure is
 * worse than a visible one — hence the rollback rather than a shrug.
 */
function SaveControls({
  turnId,
  bookmarked,
  note,
  onBookmark,
  onNote,
}: {
  turnId: number;
  bookmarked: boolean;
  note: string;
  onBookmark: (turnId: number, bookmarked: boolean) => Promise<void>;
  onNote?: (turnId: number, note: string) => Promise<void>;
}) {
  const [saved, setSaved] = useState(bookmarked);
  const [draft, setDraft] = useState(note);
  const [editing, setEditing] = useState(false);
  const [failed, setFailed] = useState(false);

  // Re-sync when the card is reused for a different turn, or when the same
  // turn is reloaded from the server with a newer value.
  useEffect(() => setSaved(bookmarked), [bookmarked, turnId]);
  useEffect(() => setDraft(note), [note, turnId]);

  async function toggle() {
    const next = !saved;
    setSaved(next);
    setFailed(false);
    try {
      await onBookmark(turnId, next);
    } catch {
      setSaved(!next);
      setFailed(true);
    }
  }

  async function saveNote() {
    setEditing(false);
    if (draft === note || !onNote) return;
    try {
      await onNote(turnId, draft);
    } catch {
      setDraft(note);
      setFailed(true);
    }
  }

  return (
    <div className="flex items-center gap-1.5">
      <button
        onClick={() => void toggle()}
        aria-pressed={saved}
        title={saved ? "Remove from saved" : "Save this solution"}
        className={`rounded-lg border px-2.5 py-1 text-[11px] transition-colors ${
          saved
            ? "border-accent bg-accent/10 text-accent"
            : "border-line text-muted hover:border-accent hover:text-accent"
        }`}
      >
        {saved ? "★ Saved" : "☆ Save"}
      </button>

      {onNote && (
        <button
          onClick={() => setEditing((open) => !open)}
          title="Add a note"
          className={`rounded-lg border px-2.5 py-1 text-[11px] transition-colors ${
            draft
              ? "border-line text-paper"
              : "border-line text-muted hover:text-paper"
          }`}
        >
          {draft ? "Note ·" : "Note"}
        </button>
      )}

      {failed && (
        <span className="text-[11px] text-wrong" role="status">
          Not saved
        </span>
      )}

      {editing && onNote && (
        <div className="absolute inset-x-5 top-full z-10 mt-1 sm:inset-x-6">
          <textarea
            autoFocus
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onBlur={() => void saveNote()}
            onKeyDown={(e) => {
              if (e.key === "Escape") {
                setDraft(note);
                setEditing(false);
              }
            }}
            rows={2}
            placeholder="Why you got this wrong, what to remember… (searchable)"
            className="w-full resize-y rounded-xl border border-accent bg-ink px-3 py-2 text-[13px] text-paper placeholder:text-muted/60 focus:outline-none"
          />
        </div>
      )}
    </div>
  );
}

export function SolutionCard({
  solution,
  verdict,
  verified,
  totalMs,
  problem = "",
  turnId,
  bookmarked = false,
  note = "",
  onBookmark,
  onNote,
}: {
  solution: Solution;
  verdict: Verdict;
  verified: boolean;
  totalMs?: number;
  /** The question this answers. Only needed for export — a solution copied
   *  into notes without its problem is close to useless six months later. */
  problem?: string;
  /** Omit to render a card with no save controls (e.g. an unsaved solve). */
  turnId?: number | null;
  bookmarked?: boolean;
  note?: string;
  onBookmark?: (turnId: number, bookmarked: boolean) => Promise<void>;
  onNote?: (turnId: number, note: string) => Promise<void>;
}) {
  const style = VERDICT_STYLE[verdict.kind] ?? VERDICT_STYLE.error;
  const cardRef = useRef<HTMLElement>(null);

  return (
    <article ref={cardRef} className="overflow-hidden rounded-2xl border border-line bg-slate">
      {/* ── verdict ─────────────────────────────────────────────────── */}
      {/* `relative` anchors the note editor, which pops out below the row. */}
      <header className="relative px-5 py-4 sm:px-6">
        <div className="flex flex-wrap items-center gap-2">
          <span
            className={`inline-flex items-center gap-2 rounded-full border px-3 py-1 text-xs font-medium ${style.chip}`}
          >
            <span className={`h-1.5 w-1.5 rounded-full ${style.dot}`} aria-hidden />
            {style.label}
          </span>
          <span className="font-mono text-[11px] text-muted">
            {solution.topic.replace(/_/g, " ")} · {solution.difficulty.replace(/_/g, " ")}{" "}
            · ~{solution.time_minutes} min
            {totalMs != null && ` · ${(totalMs / 1000).toFixed(1)}s`}
          </span>

          {turnId != null && onBookmark && (
            <span className="ml-auto">
              <SaveControls
                turnId={turnId}
                bookmarked={bookmarked}
                note={note}
                onBookmark={onBookmark}
                onNote={onNote}
              />
            </span>
          )}
        </div>
        <p className="mt-2 text-xs leading-relaxed text-muted">{style.blurb}</p>

        {/* The computed value, shown only when it contradicts the answer. */}
        {!verified && verdict.expected && (
          <div className="mt-3 rounded-lg border border-wrong/30 bg-wrong/5 p-3">
            <p className="text-xs text-muted">
              The verifier computed{" "}
              <code className="font-mono text-wrong">{verdict.expected}</code>
              {verdict.claimed && (
                <>
                  , but this answer claims{" "}
                  <code className="font-mono text-paper">{verdict.claimed}</code>
                </>
              )}
              .
            </p>
          </div>
        )}
      </header>

      {/* ── the answer ──────────────────────────────────────────────── */}
      <section className="border-t border-line px-5 py-4 sm:px-6">
        <div className="mb-3 flex items-center gap-2">
          <h3 className="font-mono text-[11px] tracking-[0.16em] text-muted uppercase">
            Answer
          </h3>
          {/* Right where the answer is, because copying the answer is the
              single most common thing anyone does with this card. */}
          <CopyButton
            text={() => answerAsLatex(solution)}
            label="Copy LaTeX"
            copiedLabel="LaTeX copied"
            title="Copy the answer as $$…$$, ready for Overleaf or Markdown"
            className="no-print ml-auto"
          />
        </div>
        {solution.answer_latex ? (
          <Math latex={solution.answer_latex} display className="text-lg" />
        ) : (
          <MathText className="text-lg">{solution.final_answer}</MathText>
        )}
      </section>

      {/* ── the derivation ──────────────────────────────────────────── */}
      <Section title={`Solution · ${solution.steps.length} steps`}>
        <ol className="space-y-5">
          {solution.steps.map((step) => (
            <li key={step.number} className="flex gap-3.5">
              <span className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full border border-line font-mono text-[11px] text-muted">
                {step.number}
              </span>
              <div className="prose-math min-w-0 flex-1">
                <MathText className="text-[15px]">{step.action}</MathText>
                {step.expression && (
                  <Math latex={step.expression} display className="my-2.5" />
                )}
                {/* The justification is what makes this teaching rather than
                    a list of expressions, so it is always shown. */}
                <p className="mt-1.5 border-l-2 border-line pl-3 text-[13px] text-muted">
                  <MathText>{step.justification}</MathText>
                </p>
              </div>
            </li>
          ))}
        </ol>
      </Section>

      {solution.formulas_used.length > 0 && (
        <Section title="Formulas used">
          <ul className="space-y-2">
            {solution.formulas_used.map((formula, i) => (
              <li key={i} className="overflow-x-auto">
                <Math latex={formula} />
              </li>
            ))}
          </ul>
        </Section>
      )}

      {solution.common_mistakes.length > 0 && (
        <Section title="Common mistakes">
          <ul className="space-y-2.5">
            {solution.common_mistakes.map((mistake, i) => (
              <li key={i} className="flex gap-2.5 text-[14px] leading-relaxed">
                <span className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-unverified" aria-hidden />
                <MathText className="text-muted">{mistake}</MathText>
              </li>
            ))}
          </ul>
        </Section>
      )}

      {solution.alternative_method && (
        <Section title="Another way">
          <MathText className="text-[14px] leading-relaxed text-muted">
            {solution.alternative_method}
          </MathText>
        </Section>
      )}

      {solution.concepts.length > 0 && (
        <Section title="Concepts">
          <div className="flex flex-wrap gap-1.5">
            {solution.concepts.map((concept, i) => (
              <span
                key={i}
                className="rounded-full border border-line px-2.5 py-1 text-[12px] text-muted"
              >
                {concept}
              </span>
            ))}
          </div>
        </Section>
      )}

      {solution.practice_question && (
        <Section title="Try this next">
          <MathText className="text-[14px] leading-relaxed">
            {solution.practice_question}
          </MathText>
        </Section>
      )}

      {/* ── take it somewhere else ──────────────────────────────────── */}
      <div className="no-print flex flex-wrap items-center gap-2 border-t border-line px-5 py-3 sm:px-6">
        <CopyButton
          text={() => solutionAsMarkdown(problem, solution, verdict, verified)}
          label="Copy as Markdown"
          copiedLabel="Copied"
          title="The whole solution, for notes — Obsidian, Notion, a README"
        />
        <button
          onClick={() => printElement(cardRef.current)}
          title="Opens your print dialogue — choose Save as PDF"
          className="rounded-lg border border-line px-2.5 py-1 text-[11px] text-muted transition-colors hover:border-accent hover:text-accent"
        >
          Save as PDF
        </button>
      </div>

      {/* What was actually checked. Collapsed by default — most students do
          not care, and the ones who do not trust the badge deserve to see it. */}
      {verdict.checks.length > 0 && (
        <details className="border-t border-line px-5 py-3 sm:px-6">
          <summary className="cursor-pointer font-mono text-[11px] uppercase tracking-[0.16em] text-muted hover:text-paper">
            What was checked
          </summary>
          <ul className="mt-3 space-y-1.5">
            {verdict.checks.map((check, i) => (
              <li key={i} className="overflow-x-auto font-mono text-[12px] text-muted">
                {check}
              </li>
            ))}
          </ul>
          <p className="mt-3 font-mono text-[11px] text-muted/70">{verdict.detail}</p>
        </details>
      )}
    </article>
  );
}
