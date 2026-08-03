/**
 * Renders a review of a student's own working.
 * ═══════════════════════════════════════════════════════════════════════
 *
 * WHAT LEADS, AND WHY
 *   `student_was_right` is the first thing on the card, because it is the
 *   question that was asked. Everything else — the mistake, the corrected
 *   route, the concept to revise — answers "why", and none of it lands if the
 *   student is still scanning for whether they got it.
 *
 * THE OVERRIDE IS SHOWN, NOT HIDDEN
 *   When SymPy contradicts the reviewer and the verdict is corrected, the
 *   card says so. It is tempting to hide it — it exposes the model being
 *   wrong — but a student who was told "your answer is right, the reviewer
 *   disagreed and was overruled" has learned something true about the tool
 *   they are using. Quietly rewriting the judgement would not.
 *
 * A MISTAKE IS NOT A TELLING-OFF
 *   Each mistake shows the quoted line, the rule it breaks, and the fix.
 *   `why_it_is_wrong` is given the most visual weight of the three, because
 *   it is the only part that generalises to the next problem.
 */

"use client";

import { Math, MathText } from "./MathText";
import {
  ERROR_LABEL,
  VERDICT_STYLE,
  type Mistake,
  type ReviewResponse,
} from "@/lib/review";

const TONE: Record<string, { chip: string; dot: string; text: string }> = {
  good: {
    chip: "border-verified/40 bg-verified/10 text-verified",
    dot: "bg-verified",
    text: "text-verified",
  },
  mixed: {
    chip: "border-unverified/40 bg-unverified/10 text-unverified",
    dot: "bg-unverified",
    text: "text-unverified",
  },
  bad: {
    chip: "border-wrong/40 bg-wrong/10 text-wrong",
    dot: "bg-wrong",
    text: "text-wrong",
  },
  neutral: {
    chip: "border-muted/40 bg-muted/10 text-muted",
    dot: "bg-muted",
    text: "text-muted",
  },
};

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="border-t border-line px-5 py-4 sm:px-6">
      <h3 className="mb-3 font-mono text-[11px] tracking-[0.16em] text-muted uppercase">
        {title}
      </h3>
      {children}
    </section>
  );
}

function MistakeItem({ mistake }: { mistake: Mistake }) {
  const fatal = mistake.severity === "fatal";

  return (
    <li className="rounded-xl border border-line p-4">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        {mistake.line > 0 && (
          <span className="font-mono text-[11px] text-muted">line {mistake.line}</span>
        )}
        <span
          className={`rounded-full border px-2 py-0.5 text-[10px] ${
            fatal
              ? "border-wrong/40 bg-wrong/10 text-wrong"
              : "border-muted/40 bg-muted/10 text-muted"
          }`}
        >
          {ERROR_LABEL[mistake.type] ?? mistake.type}
          {!fatal && " · minor"}
        </span>
      </div>

      {mistake.quote && (
        <pre className="mb-2.5 overflow-x-auto rounded-lg bg-ink px-3 py-2 font-mono text-[12px] text-muted">
          {mistake.quote}
        </pre>
      )}

      <MathText className="text-[14px] leading-relaxed">
        {mistake.what_went_wrong}
      </MathText>

      {/* The rule, given the most weight — it is the only part that
          generalises to the next problem. */}
      {mistake.why_it_is_wrong && (
        <p className="mt-2.5 border-l-2 border-accent/50 pl-3 text-[13px] leading-relaxed text-muted">
          <MathText>{mistake.why_it_is_wrong}</MathText>
        </p>
      )}

      {mistake.correction && (
        <div className="mt-3">
          <span className="mb-1 block font-mono text-[10px] tracking-[0.14em] text-muted uppercase">
            Should be
          </span>
          <div className="overflow-x-auto rounded-lg border border-verified/30 bg-verified/5 px-3 py-2">
            <Math latex={mistake.correction} />
          </div>
        </div>
      )}
    </li>
  );
}

export function ReviewCard({ result }: { result: ReviewResponse }) {
  const { review } = result;
  const style = VERDICT_STYLE[review.verdict] ?? VERDICT_STYLE.unclear;
  const tone = TONE[style.tone];

  return (
    <article className="overflow-hidden rounded-2xl border border-line bg-slate">
      {/* ── the answer to the question they asked ───────────────────── */}
      <header className="px-5 py-4 sm:px-6">
        <div className="flex flex-wrap items-center gap-2">
          <span
            className={`inline-flex items-center gap-2 rounded-full border px-3 py-1 text-xs font-medium ${tone.chip}`}
          >
            <span className={`h-1.5 w-1.5 rounded-full ${tone.dot}`} aria-hidden />
            {style.label}
          </span>
          <span className="font-mono text-[11px] text-muted">
            {review.topic.replace(/_/g, " ")} · {review.difficulty.replace(/_/g, " ")}
            {result.total_ms > 0 && ` · ${(result.total_ms / 1000).toFixed(1)}s`}
          </span>

          {!result.verified && (
            <span
              className="rounded-full border border-unverified/40 bg-unverified/10 px-2 py-0.5 text-[10px] text-unverified"
              title="The reference answer this review rests on could not be recomputed, so the review is one model's opinion."
            >
              unchecked
            </span>
          )}
        </div>

        <p className="mt-2 text-xs leading-relaxed text-muted">{style.blurb}</p>

        {/* SymPy overruled the reviewer. Said plainly — the student should
            know the tool corrected itself. */}
        {result.overridden_from && (
          <div className="mt-3 rounded-lg border border-accent/30 bg-accent/5 p-3">
            <p className="text-xs leading-relaxed text-muted">
              The reviewer first marked this{" "}
              <span className="font-medium text-paper">
                {VERDICT_STYLE[result.overridden_from]?.label.toLowerCase() ??
                  result.overridden_from}
              </span>
              . The computer algebra system recomputed the answer and disagreed, so
              the verdict above was corrected.
            </p>
          </div>
        )}

        {review.summary && (
          <p className="mt-3 text-[14px] leading-relaxed">
            <MathText>{review.summary}</MathText>
          </p>
        )}
      </header>

      {/* ── what they did right ─────────────────────────────────────── */}
      {review.what_went_well && (
        <Section title="What worked">
          <MathText className="text-[14px] leading-relaxed text-muted">
            {review.what_went_well}
          </MathText>
        </Section>
      )}

      {/* ── the mistakes ────────────────────────────────────────────── */}
      {review.mistakes.length > 0 ? (
        <Section
          title={
            review.mistakes.length === 1
              ? "Where it turned"
              : `${review.mistakes.length} things to fix`
          }
        >
          <ul className="space-y-3">
            {review.mistakes.map((mistake, i) => (
              <MistakeItem key={i} mistake={mistake} />
            ))}
          </ul>
        </Section>
      ) : (
        <Section title="Mistakes">
          <p className="text-[14px] text-muted">
            None found. The working holds up.
          </p>
        </Section>
      )}

      {/* ── the correct route from the mistake onward ───────────────── */}
      {review.corrected_working.length > 0 && (
        <Section title="From there, correctly">
          <ol className="space-y-2.5">
            {review.corrected_working.map((line, i) => (
              <li key={i} className="overflow-x-auto">
                <Math latex={line} />
              </li>
            ))}
          </ol>
        </Section>
      )}

      <Section title="Correct answer">
        {review.correct_answer_latex ? (
          <Math latex={review.correct_answer_latex} display className="text-lg" />
        ) : (
          <MathText className="text-lg">{review.correct_answer}</MathText>
        )}
      </Section>

      {review.concept_to_review && (
        <Section title="Worth revising">
          <MathText className="text-[14px] text-muted">
            {review.concept_to_review}
          </MathText>
        </Section>
      )}

      {/* What was actually checked. Collapsed — most students do not care,
          and the ones who do not trust the badge deserve to see it. */}
      <details className="border-t border-line px-5 py-3 sm:px-6">
        <summary className="cursor-pointer font-mono text-[11px] tracking-[0.16em] text-muted uppercase hover:text-paper">
          What was checked
        </summary>
        <dl className="mt-3 space-y-2 font-mono text-[12px] text-muted">
          <div>
            <dt className="inline text-muted/70">reference answer: </dt>
            <dd className="inline">
              {result.answer_check.kind} — {result.answer_check.detail}
            </dd>
          </div>
          <div>
            <dt className="inline text-muted/70">your answer: </dt>
            <dd className="inline">
              {result.student_check.kind} — {result.student_check.detail}
            </dd>
          </div>
        </dl>
      </details>
    </article>
  );
}
