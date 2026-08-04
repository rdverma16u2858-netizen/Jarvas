/**
 * The name, in one place.
 * ═══════════════════════════════════════════════════════════════════════
 *
 * WHY A COMPONENT RATHER THAN THE TEXT REPEATED
 *   The title appears on the chat page, the login screen and the waking
 *   screen. Three copies drift: one gets renamed, the others do not, and the
 *   app quietly disagrees with itself about what it is called.
 */

export function Brand({ className = "" }: { className?: string }) {
  return (
    <div className={className}>
      <h1 className="font-display text-3xl font-bold tracking-tight">
        JARVAS{" "}
        {/* Baseline-aligned and much smaller: it is a description, not part of
            the name, and setting it at the same weight would read as a
            two-word title. */}
        <span className="align-baseline text-sm font-normal tracking-normal text-muted">
          (the math bot)
        </span>
      </h1>
      <p className="mt-1 font-mono text-[10px] tracking-[0.14em] text-muted/70 uppercase">
        made by Rudra Verma · founder of Pixelforge
      </p>
    </div>
  );
}
