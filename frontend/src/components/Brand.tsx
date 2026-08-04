/**
 * The wordmark.
 * ═══════════════════════════════════════════════════════════════════════
 *
 * WHY A COMPONENT RATHER THAN THE TEXT REPEATED
 *   The title appears on the chat page, the login screen and the waking
 *   screen. Three copies drift: one gets renamed, the others do not, and the
 *   app quietly disagrees with itself about what it is called.
 *
 * THE HIERARCHY IS THE DESIGN
 *   Three lines of similar weight is a paragraph, not a wordmark. Each line
 *   here steps down hard on all three axes at once — size, weight and colour —
 *   so the eye lands on JARVAS, then reads down without ever being asked to
 *   choose what to look at.
 *
 *       JARVAS      semibold, large, full-contrast, tightly tracked
 *       The Math Bot   light, ~1/4 the size, muted, widely tracked
 *       By Rudra Verma…  smallest, most muted, a credit line
 *
 * WHY SANS FOR THE NAME WHEN THE APP'S DISPLAY FACE IS A SERIF
 *   The serif is for mathematical prose — it distinguishes the tutor's
 *   explanation from UI chrome, the way a textbook does. A logotype is not
 *   prose, and the restrained geometric sans is what reads as a modern
 *   software product rather than a document.
 *
 * WHY TIGHT TRACKING ON THE NAME AND WIDE ON THE SUBTITLE
 *   Negative tracking pulls a short word into a single dense shape, which is
 *   what makes it read as a mark rather than as text. Positive tracking on the
 *   line beneath does the opposite — it opens the words out, so the two never
 *   compete even though they sit close together.
 *
 * NO GRADIENT, NO GLOW, NO ANIMATION
 *   Every one of those would be the most eye-catching thing on a page whose
 *   actual subject is a verified answer.
 */

const SIZES = {
  /** Hero: the login and waking screens, where this is the only content. */
  lg: {
    name: "text-5xl sm:text-6xl",
    subtitle: "text-sm sm:text-base",
    byline: "text-[11px]",
    gapName: "mt-4 sm:mt-5",
    gapByline: "mt-5 sm:mt-6",
  },
  /** Inline: above the chat, where the app itself is the subject. */
  md: {
    name: "text-3xl",
    subtitle: "text-[13px]",
    byline: "text-[10px]",
    gapName: "mt-2.5",
    gapByline: "mt-3",
  },
} as const;

export function Brand({
  size = "md",
  className = "",
}: {
  size?: keyof typeof SIZES;
  className?: string;
}) {
  const s = SIZES[size];

  return (
    <div className={`text-center ${className}`}>
      <h1
        className={`font-sans ${s.name} font-semibold leading-none tracking-[-0.045em] text-paper`}
      >
        JARVAS
      </h1>

      {/* Light weight and open tracking: the product descriptor should feel
          set rather than shouted, and the contrast in letterfit is what keeps
          it from reading as a second half of the name. */}
      <p
        className={`${s.gapName} ${s.subtitle} font-light tracking-[0.22em] text-muted uppercase`}
      >
        The Math Bot
      </p>

      {/* A credit line. Sentence case rather than uppercase — at this length
          uppercase turns into a block of texture, and the point is that it
          recedes. */}
      <p className={`${s.gapByline} ${s.byline} tracking-[0.04em] text-muted/55`}>
        By Rudra Verma · Founder of PixelForge
      </p>
    </div>
  );
}
