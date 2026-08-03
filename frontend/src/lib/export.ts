/**
 * Turning a solution into text a student can take somewhere else.
 * ═══════════════════════════════════════════════════════════════════════
 *
 * THREE FORMATS, THREE DESTINATIONS
 *   LaTeX     the answer alone, for pasting into an assignment or Overleaf
 *   Markdown  the whole solution, for notes — Obsidian, Notion, a repo README
 *   plain     the problem text, for pasting back into a search or a message
 *
 *   They exist separately because copying "the solution" means different
 *   things depending on where it is going, and a single button that guesses
 *   is wrong two times out of three.
 *
 * WHY THE VERDICT IS ALWAYS INCLUDED IN THE MARKDOWN
 *   A solution copied into notes outlives the app it came from. Six months
 *   later the student has a page of mathematics with no indication of whether
 *   any of it was checked — and this whole product exists on the premise that
 *   the difference matters. So the verification line travels with it.
 */

import type { Solution, Verdict } from "./solve";

/** The `$$...$$` form, so it pastes into Markdown and Overleaf alike. */
export function answerAsLatex(solution: Solution): string {
  const body = solution.answer_latex || solution.final_answer;
  return `$$${body}$$`;
}

function verdictLine(verdict: Verdict, verified: boolean): string {
  if (verified) return "> **Verified** — recomputed independently by a computer algebra system.";
  if (verdict.kind === "refuted") {
    const computed = verdict.expected ? ` It computed \`${verdict.expected}\`.` : "";
    return `> **Failed verification** — the check disagreed with this answer.${computed}`;
  }
  if (verdict.kind === "unverifiable") {
    return "> **Not checkable** — a proof or descriptive answer, with no single value to recompute.";
  }
  return "> **Unchecked** — the verifier could not evaluate this answer.";
}

/**
 * The whole solution as Markdown.
 *
 * Mathematics is left as `$...$` / `$$...$$` rather than being converted to
 * Unicode: every destination worth pasting into renders LaTeX, and Unicode
 * loses the structure of anything with a fraction or an integral in it.
 */
export function solutionAsMarkdown(
  problem: string,
  solution: Solution,
  verdict: Verdict,
  verified: boolean,
): string {
  const lines: string[] = [];

  lines.push(`# ${problem.trim()}`, "");
  lines.push(verdictLine(verdict, verified), "");

  lines.push("## Answer", "");
  lines.push(answerAsLatex(solution), "");

  if (solution.steps.length > 0) {
    lines.push("## Solution", "");
    for (const step of solution.steps) {
      lines.push(`**${step.number}. ${step.action}**`, "");
      if (step.expression) lines.push(`$$${step.expression}$$`, "");
      if (step.justification) lines.push(`> ${step.justification}`, "");
    }
  }

  if (solution.formulas_used.length > 0) {
    lines.push("## Formulas used", "");
    for (const formula of solution.formulas_used) lines.push(`- $${formula}$`);
    lines.push("");
  }

  if (solution.common_mistakes.length > 0) {
    lines.push("## Common mistakes", "");
    for (const mistake of solution.common_mistakes) lines.push(`- ${mistake}`);
    lines.push("");
  }

  if (solution.alternative_method) {
    lines.push("## Another way", "", solution.alternative_method, "");
  }

  if (solution.concepts.length > 0) {
    lines.push("## Concepts", "", solution.concepts.join(" · "), "");
  }

  if (solution.practice_question) {
    lines.push("## Try this next", "", solution.practice_question, "");
  }

  lines.push("---", `*${solution.topic.replace(/_/g, " ")} · ${solution.difficulty.replace(/_/g, " ")} · MathBot*`);

  return lines.join("\n");
}

/**
 * Copy text to the clipboard, falling back for non-secure origins.
 *
 * `navigator.clipboard` is undefined outside a secure context, which includes
 * `http://` on a LAN address — exactly how someone tests this from their
 * phone. The fallback is deprecated and still the only thing that works there.
 */
export async function copyText(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    /* fall through to the legacy path */
  }

  try {
    const area = document.createElement("textarea");
    area.value = text;
    // Kept off-screen rather than hidden: a display:none element cannot be
    // selected, so the copy silently does nothing.
    area.style.position = "fixed";
    area.style.left = "-9999px";
    document.body.appendChild(area);
    area.select();
    const ok = document.execCommand("copy");
    document.body.removeChild(area);
    return ok;
  } catch {
    return false;
  }
}

/**
 * Print one element as a PDF, via the browser's own print dialogue.
 *
 * WHY NOT html2canvas / jsPDF
 *   KaTeX has already rendered the mathematics as styled HTML. The browser
 *   prints that as vector text — selectable, searchable, sharp at any zoom.
 *   Rasterising it to a canvas first would produce a blurry bitmap of the one
 *   thing on the page that most needs to be legible, and would add a large
 *   dependency to do it.
 *
 *   "Save as PDF" is already in every print dialogue on every platform.
 *
 * The class is set on <html> so the print stylesheet can hide everything that
 * is not this element or one of its ancestors, and is removed afterwards even
 * if the user cancels — `afterprint` fires either way.
 */
export function printElement(element: HTMLElement | null): void {
  if (!element) return;

  element.classList.add("print-me");
  document.documentElement.classList.add("printing-one");

  const cleanup = () => {
    element.classList.remove("print-me");
    document.documentElement.classList.remove("printing-one");
    window.removeEventListener("afterprint", cleanup);
  };
  window.addEventListener("afterprint", cleanup);

  window.print();

  // Safari does not always fire afterprint. A timer guarantees the page is
  // not left in its print-only state, which would look like a broken render.
  setTimeout(cleanup, 1000);
}
