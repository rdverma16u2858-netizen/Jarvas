/**
 * Conversation sidebar — threads, search, and saved solutions.
 * ═══════════════════════════════════════════════════════════════════════
 *
 * THREE VIEWS, ONE PANEL
 *   Chats    every thread, newest activity first
 *   Saved    bookmarked turns
 *   Search   results, shown only while there is a query
 *
 *   They share one panel because they answer the same question — "where was
 *   that problem I did?" — and splitting them across routes would mean losing
 *   the current solve to go and look.
 *
 * WHY SEARCH IS DEBOUNCED
 *   Every keystroke would otherwise be a request, and the results would race:
 *   a slow response for "int" can land after a fast one for "integral" and
 *   overwrite it with staler results. The delay collapses the burst, and the
 *   request id check below discards any reply that is no longer the newest.
 *
 * ON MOBILE
 *   The panel is a drawer over the chat rather than a column beside it —
 *   below `lg` there is not enough width for both, and a 40%-width sidebar
 *   would leave the maths unreadable.
 */

"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import { NetworkError, UnauthorizedError } from "@/lib/api";

import {
  archiveConversation,
  deleteConversation,
  listBookmarks,
  listConversations,
  renameConversation,
  searchHistory,
  type ConversationSummary,
  type TurnDetail,
  type TurnSummary,
} from "@/lib/history";

type View = "chats" | "saved";

/** Milliseconds of quiet before a search fires. */
const SEARCH_DEBOUNCE_MS = 250;

function relativeTime(iso: string | null): string {
  if (!iso) return "";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "";

  const seconds = Math.max(0, (Date.now() - then) / 1000);
  if (seconds < 60) return "just now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  if (seconds < 604800) return `${Math.floor(seconds / 86400)}d ago`;
  return new Date(iso).toLocaleDateString();
}

export function Sidebar({
  activeId,
  onOpenConversation,
  onOpenTurn,
  onNewChat,
  open,
  onClose,
  reloadToken,
}: {
  activeId: number | null;
  onOpenConversation: (id: number) => void;
  onOpenTurn: (turn: TurnSummary | TurnDetail) => void;
  onNewChat: () => void;
  open: boolean;
  onClose: () => void;
  /** Changing this value re-fetches the lists — bumped after each solve. */
  reloadToken: number;
}) {
  const [view, setView] = useState<View>("chats");
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [saved, setSaved] = useState<TurnDetail[]>([]);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<TurnSummary[] | null>(null);
  const [searching, setSearching] = useState(false);
  const [error, setError] = useState("");

  // Identifies the newest in-flight search so late replies can be dropped.
  const searchSeq = useRef(0);

  /**
   * Navigating anywhere clears the search.
   *
   * Results hide the tab bar, so leaving a stale query in place after opening
   * a thread means the sidebar keeps showing the search you have finished with
   * and hides the thread you are now reading.
   */
  function navigate(action: () => void) {
    action();
    setQuery("");
    onClose();
  }

  /**
   * Turn a failure into something true.
   *
   * These three read identically to a user and mean entirely different
   * things. Reporting a 401 as "cannot reach the backend" sends someone to
   * check whether their server is running when in fact they are signed out —
   * and the server is answering perfectly.
   *
   * A 401 gets no message at all: `apiFetch` has already announced it, and
   * the whole app is about to swap to the login screen. A message here would
   * flash and vanish.
   */
  function describe(caught: unknown): string {
    if (caught instanceof UnauthorizedError) return "";
    if (caught instanceof NetworkError) return "Cannot reach the backend.";
    return "Could not load your chats.";
  }

  const loadConversations = useCallback(async () => {
    try {
      setConversations(await listConversations());
      setError("");
    } catch (caught) {
      setError(describe(caught));
    }
  }, []);

  const loadSaved = useCallback(async () => {
    try {
      setSaved(await listBookmarks());
      setError("");
    } catch (caught) {
      setError(describe(caught));
    }
  }, []);

  useEffect(() => {
    void loadConversations();
  }, [loadConversations, reloadToken]);

  useEffect(() => {
    if (view === "saved") void loadSaved();
  }, [view, loadSaved, reloadToken]);

  // ── search ──────────────────────────────────────────────────────────
  useEffect(() => {
    const term = query.trim();
    if (!term) {
      setResults(null);
      setSearching(false);
      return;
    }

    setSearching(true);
    const seq = ++searchSeq.current;
    const timer = setTimeout(async () => {
      try {
        const hits = await searchHistory(term);
        // Drop the reply if a newer search has started since.
        if (seq === searchSeq.current) setResults(hits);
      } catch {
        if (seq === searchSeq.current) setResults([]);
      } finally {
        if (seq === searchSeq.current) setSearching(false);
      }
    }, SEARCH_DEBOUNCE_MS);

    return () => clearTimeout(timer);
  }, [query]);

  async function rename(conversation: ConversationSummary) {
    const title = window.prompt("Rename this chat", conversation.title);
    if (title === null || !title.trim()) return;
    await renameConversation(conversation.id, title.trim());
    await loadConversations();
  }

  async function archive(id: number) {
    // Archiving, not deleting, is the default destructive-looking action:
    // it is reversible, and history that took real quota to produce should
    // not be one misclick from gone.
    await archiveConversation(id, true);
    await loadConversations();
  }

  async function remove(conversation: ConversationSummary) {
    const confirmed = window.confirm(
      `Delete "${conversation.title}" and all ${conversation.turn_count} of its solutions?\n\nThis cannot be undone.`,
    );
    if (!confirmed) return;
    await deleteConversation(conversation.id);
    if (conversation.id === activeId) onNewChat();
    await loadConversations();
  }

  return (
    <>
      {/* Scrim — mobile only; tapping outside the drawer closes it. */}
      {open && (
        <button
          aria-label="Close history"
          onClick={onClose}
          className="fixed inset-0 z-30 bg-ink/70 backdrop-blur-sm lg:hidden"
        />
      )}

      {/* Slides in as a drawer below `lg`; a fixed column at `lg` and up.
          The `lg:` override sits in the closed branch only, so the two
          branches never both set the translate. */}
      <aside
        className={`fixed inset-y-0 left-0 z-40 flex w-[17rem] flex-col border-r border-line bg-ink transition-transform ${
          open ? "translate-x-0" : "-translate-x-full lg:translate-x-0"
        }`}
      >
        {/* ── new chat ────────────────────────────────────────────── */}
        <div className="border-b border-line p-3">
          <button
            onClick={() => navigate(onNewChat)}
            className="w-full rounded-xl border border-line px-3 py-2 text-left text-sm text-paper transition-colors hover:border-accent hover:text-accent"
          >
            <span className="mr-1.5 text-muted">+</span> New chat
          </button>

          <Link
            href="/practice"
            className="mt-2 block w-full rounded-xl border border-line px-3 py-2 text-left text-sm text-paper transition-colors hover:border-accent hover:text-accent"
          >
            <span className="mr-1.5 text-muted">◎</span> Practice questions
          </Link>

          <Link
            href="/check"
            className="mt-2 block w-full rounded-xl border border-line px-3 py-2 text-left text-sm text-paper transition-colors hover:border-accent hover:text-accent"
          >
            <span className="mr-1.5 text-muted">✓</span> Check my work
          </Link>

          <Link
            href="/quiz"
            className="mt-2 block w-full rounded-xl border border-line px-3 py-2 text-left text-sm text-paper transition-colors hover:border-accent hover:text-accent"
          >
            <span className="mr-1.5 text-muted">⏱</span> Quiz &amp; mock test
          </Link>

          <Link
            href="/scan"
            className="mt-2 block w-full rounded-xl border border-line px-3 py-2 text-left text-sm text-paper transition-colors hover:border-accent hover:text-accent"
          >
            <span className="mr-1.5 text-muted">▣</span> Scan a photo
          </Link>

          <Link
            href="/progress"
            className="mt-2 block w-full rounded-xl border border-line px-3 py-2 text-left text-sm text-paper transition-colors hover:border-accent hover:text-accent"
          >
            <span className="mr-1.5 text-muted">▲</span> Progress
          </Link>

          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search problems and notes…"
            aria-label="Search history"
            className="mt-2.5 w-full rounded-lg border border-line bg-slate px-3 py-2 text-[13px] text-paper placeholder:text-muted/60 focus:border-accent focus:outline-none"
          />
        </div>

        {/* ── tabs ────────────────────────────────────────────────── */}
        {results === null && (
          <div className="flex gap-1 border-b border-line px-3 py-2">
            {(["chats", "saved"] as View[]).map((option) => (
              <button
                key={option}
                onClick={() => setView(option)}
                className={`rounded-lg px-2.5 py-1 font-mono text-[11px] uppercase tracking-[0.14em] transition-colors ${
                  view === option
                    ? "bg-slate text-paper"
                    : "text-muted hover:text-paper"
                }`}
              >
                {option}
              </button>
            ))}
          </div>
        )}

        {/* ── list ────────────────────────────────────────────────── */}
        <div className="flex-1 overflow-y-auto p-2">
          {error && <p className="px-2 py-3 text-[12px] text-wrong">{error}</p>}

          {/* Search results replace whichever tab is showing. */}
          {results !== null ? (
            searching ? (
              <p className="px-2 py-3 text-[12px] text-muted">Searching…</p>
            ) : results.length === 0 ? (
              <p className="px-2 py-3 text-[12px] text-muted">
                Nothing matches “{query.trim()}”.
              </p>
            ) : (
              <ul className="space-y-1">
                {results.map((turn) => (
                  <li key={turn.id}>
                    <button
                      onClick={() => navigate(() => onOpenTurn(turn))}
                      className="w-full rounded-lg px-2.5 py-2 text-left transition-colors hover:bg-slate"
                    >
                      <span className="line-clamp-2 text-[13px] text-paper">
                        {turn.problem}
                      </span>
                      <span className="mt-1 flex items-center gap-1.5 font-mono text-[10px] text-muted">
                        <span
                          className={
                            turn.verified ? "text-verified" : "text-unverified"
                          }
                          aria-hidden
                        >
                          ●
                        </span>
                        {turn.topic.replace(/_/g, " ")}
                        {turn.bookmarked && <span className="text-accent">★</span>}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            )
          ) : view === "chats" ? (
            conversations.length === 0 ? (
              <p className="px-2 py-3 text-[12px] text-muted">
                No chats yet. Solve something and it will appear here.
              </p>
            ) : (
              <ul className="space-y-1">
                {conversations.map((conversation) => (
                  <li key={conversation.id} className="group relative">
                    <button
                      onClick={() => navigate(() => onOpenConversation(conversation.id))}
                      className={`w-full rounded-lg px-2.5 py-2 pr-16 text-left transition-colors ${
                        conversation.id === activeId
                          ? "bg-slate text-paper"
                          : "text-muted hover:bg-slate hover:text-paper"
                      }`}
                    >
                      <span className="line-clamp-2 text-[13px]">
                        {conversation.title}
                      </span>
                      <span className="mt-0.5 block font-mono text-[10px] text-muted">
                        {conversation.turn_count}{" "}
                        {conversation.turn_count === 1 ? "problem" : "problems"}
                        {conversation.last_turn_at &&
                          ` · ${relativeTime(conversation.last_turn_at)}`}
                      </span>
                    </button>

                    {/* Revealed on hover; always reachable by keyboard. */}
                    <span className="absolute top-1.5 right-1.5 flex gap-0.5 opacity-0 transition-opacity group-focus-within:opacity-100 group-hover:opacity-100">
                      <button
                        onClick={() => void rename(conversation)}
                        title="Rename"
                        className="rounded px-1.5 py-0.5 text-[11px] text-muted hover:text-paper"
                      >
                        ✎
                      </button>
                      <button
                        onClick={() => void archive(conversation.id)}
                        title="Archive"
                        className="rounded px-1.5 py-0.5 text-[11px] text-muted hover:text-paper"
                      >
                        ⌫
                      </button>
                      <button
                        onClick={() => void remove(conversation)}
                        title="Delete permanently"
                        className="rounded px-1.5 py-0.5 text-[11px] text-muted hover:text-wrong"
                      >
                        ✕
                      </button>
                    </span>
                  </li>
                ))}
              </ul>
            )
          ) : saved.length === 0 ? (
            <p className="px-2 py-3 text-[12px] text-muted">
              Nothing saved yet. Use ☆ Save on a solution to keep it here.
            </p>
          ) : (
            <ul className="space-y-1">
              {saved.map((turn) => (
                <li key={turn.id}>
                  <button
                    onClick={() => navigate(() => onOpenTurn(turn))}
                    className="w-full rounded-lg px-2.5 py-2 text-left transition-colors hover:bg-slate"
                  >
                    <span className="line-clamp-2 text-[13px] text-paper">
                      {turn.problem}
                    </span>
                    {turn.note && (
                      <span className="mt-1 line-clamp-1 block text-[11px] text-muted italic">
                        {turn.note}
                      </span>
                    )}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </aside>
    </>
  );
}
