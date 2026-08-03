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

import { Math, MathText } from "./MathText";
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

export function SolutionCard({
  solution,
  verdict,
  verified,
  totalMs,
}: {
  solution: Solution;
  verdict: Verdict;
  verified: boolean;
  totalMs?: number;
}) {
  const style = VERDICT_STYLE[verdict.kind] ?? VERDICT_STYLE.error;

  return (
    <article className="overflow-hidden rounded-2xl border border-line bg-slate">
      {/* ── verdict ─────────────────────────────────────────────────── */}
      <header className="px-5 py-4 sm:px-6">
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
      <Section title="Answer">
        {solution.answer_latex ? (
          <Math latex={solution.answer_latex} display className="text-lg" />
        ) : (
          <MathText className="text-lg">{solution.final_answer}</MathText>
        )}
      </Section>

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
