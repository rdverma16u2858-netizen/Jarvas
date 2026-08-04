/**
 * Root layout — wraps every page in the app.
 * ═══════════════════════════════════════════════════════════════════════
 *
 * WHY THIS FILE IS REQUIRED
 *   Next.js App Router mandates a root layout. It is the only place the
 *   <html> and <body> tags exist, so anything that must appear on every
 *   page (fonts, theme class, global CSS, later: the chat sidebar) belongs
 *   here rather than being repeated per page.
 *
 * WHY IT IS A SERVER COMPONENT
 *   No "use client" directive, so this renders on the server and ships zero
 *   JavaScript to the browser. Only components that need state or event
 *   handlers opt into the client — page.tsx does, this does not.
 */

import type { Metadata, Viewport } from "next";
import "./globals.css";

import { AuthGate } from "@/components/AuthGate";

/**
 * Page metadata. Next.js turns this into <title>, <meta> and Open Graph tags,
 * so no hand-written <head> is needed.
 */
export const metadata: Metadata = {
  title: {
    default: "JARVAS — the math bot",
    // Child pages set only their own title; this appends the suffix.
    template: "%s · JARVAS",
  },
  description:
    "Solve advanced mathematics with step-by-step explanations, multiple methods, " +
    "and every answer verified symbolically before you see it. " +
    "Made by Rudra Verma, founder of Pixelforge.",
};

/**
 * Viewport is a separate export in the App Router (it used to live inside
 * `metadata`, which is now deprecated).
 *
 * `maximumScale` is deliberately left at the default so pinch-zoom keeps
 * working — blocking it is an accessibility failure, and matters more here
 * than usual because dense formulae are exactly what people zoom into.
 */
export const viewport: Viewport = {
  themeColor: "#0b0d12",
  width: "device-width",
  initialScale: 1,
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>
        {/* Wraps every page, so a route added later is gated without anyone
            remembering to gate it — the same closed-by-default rule the
            backend middleware follows. */}
        <AuthGate>{children}</AuthGate>
      </body>
    </html>
  );
}
