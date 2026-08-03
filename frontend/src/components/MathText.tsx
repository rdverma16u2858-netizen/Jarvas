/**
 * Renders text with embedded LaTeX.
 * ═══════════════════════════════════════════════════════════════════════
 *
 * WHY KaTeX AND NOT MathJax
 *   KaTeX renders synchronously and roughly an order of magnitude faster.
 *   A solution page here contains 20-40 separate expressions (every step,
 *   every formula), and MathJax's asynchronous typesetting makes them pop in
 *   one by one after paint. KaTeX covers everything this syllabus needs.
 *
 * WHAT THIS COMPONENT SOLVES
 *   The model returns MIXED content — prose with mathematics inside it:
 *
 *     "With $x=\tan\theta$ we get $dx=\sec^2\theta\,d\theta$, so..."
 *
 *   Rendering that whole string as LaTeX fails; rendering it as text shows
 *   raw backslashes. It has to be split on the delimiters and each part
 *   handled differently.
 *
 * WHY dangerouslySetInnerHTML IS SAFE HERE
 *   The name is alarming and the usage is not. KaTeX's `renderToString` is an
 *   HTML generator with `trust: false`, so it emits only its own markup and
 *   escapes everything else — it cannot produce a <script> tag from input.
 *   Only KaTeX output is ever passed to it; the surrounding prose goes
 *   through React as normal text and is escaped by React.
 */

"use client";

import katex from "katex";
import { useMemo } from "react";

/** Split on $$...$$ (display) and $...$ (inline), keeping the delimiters. */
const SPLIT = /(\$\$[\s\S]+?\$\$|\$[^$\n]+?\$)/g;

function render(latex: string, display: boolean): string | null {
  try {
    return katex.renderToString(latex, {
      displayMode: display,
      // Do not throw on malformed input — the model occasionally emits a
      // macro KaTeX does not know, and one bad expression must not blank the
      // whole solution.
      throwOnError: false,
      // Renders unknown commands in red rather than as an exception, so a
      // problem is visible to us without being fatal to the student.
      errorColor: "#f87171",
      trust: false,
      strict: false,
      // \ce, \text etc. that students see in exam papers.
      macros: { "\\ln": "\\operatorname{ln}", "\\RR": "\\mathbb{R}" },
    });
  } catch {
    return null;
  }
}

export function MathText({
  children,
  className = "",
}: {
  children: string;
  className?: string;
}) {
  const parts = useMemo(() => {
    const text = children ?? "";
    return text.split(SPLIT).filter(Boolean).map((part, index) => {
      const isDisplay = part.startsWith("$$") && part.endsWith("$$");
      const isInline =
        !isDisplay && part.startsWith("$") && part.endsWith("$") && part.length > 2;

      if (!isDisplay && !isInline) {
        return { key: index, kind: "text" as const, value: part };
      }

      const latex = isDisplay ? part.slice(2, -2) : part.slice(1, -1);
      const html = render(latex, isDisplay);
      return html
        ? { key: index, kind: "math" as const, display: isDisplay, value: html }
        : { key: index, kind: "text" as const, value: part };
    });
  }, [children]);

  return (
    <span className={className}>
      {parts.map((part) =>
        part.kind === "text" ? (
          <span key={part.key}>{part.value}</span>
        ) : (
          <span
            key={part.key}
            className={part.display ? "my-3 block overflow-x-auto" : "inline-block"}
            dangerouslySetInnerHTML={{ __html: part.value }}
          />
        ),
      )}
    </span>
  );
}

/**
 * Renders a bare LaTeX string — no prose, no delimiters.
 *
 * Used for fields the schema guarantees are pure LaTeX (`answer_latex`,
 * `formulas_used`), where wrapping them in $ just to have MathText strip them
 * again would be silly.
 */
export function Math({
  latex,
  display = false,
  className = "",
}: {
  latex: string;
  display?: boolean;
  className?: string;
}) {
  const html = useMemo(() => render(latex ?? "", display), [latex, display]);

  if (!html) return <code className={className}>{latex}</code>;

  return (
    <span
      className={`${display ? "block overflow-x-auto" : "inline-block"} ${className}`}
      dangerouslySetInnerHTML={{ __html: html }}
    />
  );
}
