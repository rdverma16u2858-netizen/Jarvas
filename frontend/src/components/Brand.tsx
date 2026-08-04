/**
 * The name, in one place.
 * ═══════════════════════════════════════════════════════════════════════
 *
 * WHY A COMPONENT RATHER THAN THE TEXT REPEATED
 *   The title appears on the chat page, the login screen and the waking
 *   screen. Three copies drift: one gets renamed, the others do not, and the
 *   app quietly disagrees with itself about what it is called.
 *
 * THE HIERARCHY IS THE WHOLE DESIGN
 *   Three lines competing for attention is not a wordmark, it is a paragraph.
 *   So there is exactly one thing to look at — JARVAS, large and tracked
 *   tight — and everything else recedes hard: the descriptor is small and
 *   grey on the same baseline, and the byline is smaller still, spaced out,
 *   and quiet enough to read as a signature rather than a subtitle.
 */

export function Brand({
  /** `lg` for a landing or login screen, `md` inline above the chat. */
  size = "md",
  className = "",
}: {
  size?: "md" | "lg";
  className?: string;
}) {
  const title =
    size === "lg"
      ? "text-5xl sm:text-6xl"
      : "text-4xl";

  return (
    <div className={className}>
      <div className="flex items-baseline gap-2.5">
        <h1
          className={`font-display font-bold ${title} leading-none tracking-[-0.03em] text-paper`}
        >
          JARVAS
        </h1>
        {/* Baseline-aligned, deliberately unemphatic: it describes the thing,
            it is not part of the name. Matching the title's weight would read
            as a two-word title. */}
        <span className="text-[13px] font-normal tracking-normal text-muted">
          the math bot
        </span>
      </div>

      {/* A signature, not a subtitle. Wide tracking and a small size put it
          firmly below the name in the reading order. */}
      <p className="mt-2 font-mono text-[10px] tracking-[0.2em] text-muted/60 uppercase">
        Rudra Verma · Pixelforge
      </p>
    </div>
  );
}
