/**
 * A copy button that confirms it worked.
 * ═══════════════════════════════════════════════════════════════════════
 *
 * WHY THE CONFIRMATION MATTERS MORE THAN IT LOOKS
 *   Copying is invisible. Without feedback the only way to know whether a
 *   click registered is to go and paste somewhere, and when it silently fails
 *   — a non-secure origin, a denied permission — the student discovers it in
 *   the document they are pasting into, having lost what was on the clipboard
 *   before.
 *
 *   So this reports both outcomes, and reports failure differently from
 *   success rather than just not changing.
 */

"use client";

import { useEffect, useRef, useState } from "react";

import { copyText } from "@/lib/export";

type State = "idle" | "copied" | "failed";

export function CopyButton({
  /** Either the text, or a function producing it — use a function when
   *  building the string is expensive enough to not want it on every render. */
  text,
  label = "Copy",
  copiedLabel = "Copied",
  title,
  className = "",
}: {
  text: string | (() => string);
  label?: string;
  copiedLabel?: string;
  title?: string;
  className?: string;
}) {
  const [state, setState] = useState<State>("idle");
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Clearing on unmount stops a state update on a component that has gone —
  // which React warns about and which happens constantly here, because these
  // buttons live on cards that get replaced when a new solve arrives.
  useEffect(() => {
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, []);

  async function run() {
    const ok = await copyText(typeof text === "function" ? text() : text);
    setState(ok ? "copied" : "failed");
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => setState("idle"), 1800);
  }

  const tone =
    state === "copied"
      ? "border-verified/50 text-verified"
      : state === "failed"
        ? "border-wrong/50 text-wrong"
        : "border-line text-muted hover:border-accent hover:text-accent";

  return (
    <button
      onClick={() => void run()}
      title={title ?? label}
      // Announced rather than only shown, since the whole point is confirming
      // an action with no visible result.
      aria-live="polite"
      className={`rounded-lg border px-2.5 py-1 text-[11px] whitespace-nowrap transition-colors ${tone} ${className}`}
    >
      {state === "copied" ? `✓ ${copiedLabel}` : state === "failed" ? "Copy failed" : label}
    </button>
  );
}
