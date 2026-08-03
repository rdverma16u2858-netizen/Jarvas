/**
 * The link bar every page carries.
 * ═══════════════════════════════════════════════════════════════════════
 *
 * WHY THIS EXISTS
 *   The sidebar only renders on the chat page, so until now every other
 *   screen could reach exactly one destination: back to chat. Getting from
 *   Practice to Quiz meant two navigations through a page you did not want.
 *
 *   Six screens with no way between them is not six features, it is one
 *   feature and five dead ends.
 *
 * WHY IT IS NOT THE SIDEBAR
 *   The sidebar is a conversation list that happens to have links at the top.
 *   Rendering all of it on Progress would mean loading and showing chat
 *   history on a page about mastery. This is the links, without the rest.
 */

"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const DESTINATIONS = [
  { href: "/", label: "Chat", icon: "◆" },
  { href: "/practice", label: "Practice", icon: "◎" },
  { href: "/quiz", label: "Quiz", icon: "⏱" },
  { href: "/check", label: "Check work", icon: "✓" },
  { href: "/scan", label: "Scan", icon: "▣" },
  { href: "/progress", label: "Progress", icon: "▲" },
] as const;

export function PageNav() {
  const pathname = usePathname();

  return (
    // Scrolls sideways rather than wrapping to two rows on a phone: a nav
    // that changes height between pages makes the content below it jump.
    <nav className="no-print -mx-4 mb-1 overflow-x-auto px-4 sm:-mx-6 sm:px-6">
      <ul className="flex gap-1.5 pb-1">
        {DESTINATIONS.map((destination) => {
          const active = pathname === destination.href;
          return (
            <li key={destination.href}>
              <Link
                href={destination.href}
                aria-current={active ? "page" : undefined}
                className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-[12px] whitespace-nowrap transition-colors ${
                  active
                    ? "border-accent bg-accent/10 text-accent"
                    : "border-line text-muted hover:border-accent hover:text-paper"
                }`}
              >
                <span aria-hidden className="text-[11px] opacity-70">
                  {destination.icon}
                </span>
                {destination.label}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
