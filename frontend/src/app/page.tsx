/**
 * The chat interface.
 * ═══════════════════════════════════════════════════════════════════════
 *
 * WHY THE PROGRESS DISPLAY IS SHAPED THE WAY IT IS
 *   Measured against the real API: 5s on the fast tier, 16s on balanced, 35s
 *   on deep. Thirty-five seconds of an unlabelled spinner is long enough that
 *   people assume it has hung and reload — losing the request and spending
 *   the quota for nothing.
 *
 *   So the stream reports named stages ("Working through the problem",
 *   "Checking the answer with SymPy") plus a live character count. The count
 *   is not decoration: it is the difference between "this is slow" and "this
 *   is broken", and it is the only signal that distinguishes them.
 *
 *   The generated text itself is JSON, so it is deliberately NOT shown. A
 *   student watching field names and braces scroll past learns nothing.
 *
 * WHO OWNS THE CONVERSATION ID
 *   This page does, in `conversationId`. It is null for an unsaved new chat
 *   and is adopted from the FIRST result event — the backend creates the
 *   thread as a side effect of the first solve, so the client never has to
 *   create one up front. Every later solve in the session sends it back, and
 *   that is what makes "now do the same from 0 to infinity" resolve.
 */

"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { Sidebar } from "@/components/Sidebar";
import { SolutionCard } from "@/components/SolutionCard";
import { ApiError, IS_LOCAL_API, NetworkError } from "@/lib/api";
import {
  getConversation,
  setBookmark,
  setNote,
  turnResult,
  type TurnDetail,
  type TurnSummary,
} from "@/lib/history";
import { streamSolve, type SolveResult, type Tier } from "@/lib/solve";

interface Entry {
  id: number;
  problem: string;
  tier: Tier;
  result?: SolveResult;
  error?: string;
  hint?: string;
  /** Set once the backend has saved the turn; null while unsaved. */
  turnId?: number | null;
  bookmarked?: boolean;
  note?: string;
}

const EXAMPLES = [
  "Evaluate the integral of ln(1+x)/(1+x^2) from 0 to 1",
  "Differentiate f(x) = x^2 sin(x)",
  "Solve x^2 = 4",
  "Find the limit of sin(x)/x as x approaches 0",
];

const TIERS: { value: Tier; label: string; blurb: string }[] = [
  { value: "fast", label: "Fast", blurb: "routine problems, ~5s" },
  { value: "balanced", label: "Balanced", blurb: "the default, ~15s" },
  { value: "deep", label: "Deep", blurb: "proofs & olympiad, ~35s" },
];

/** Rebuild chat entries from a stored thread. */
function entriesFrom(turns: TurnDetail[]): Entry[] {
  return turns.map((turn) => ({
    id: turn.id,
    problem: turn.problem,
    tier: (turn.tier || "balanced") as Tier,
    result: turnResult(turn),
    turnId: turn.id,
    bookmarked: turn.bookmarked,
    note: turn.note,
  }));
}

export default function ChatPage() {
  const [entries, setEntries] = useState<Entry[]>([]);
  const [input, setInput] = useState("");
  const [tier, setTier] = useState<Tier>("balanced");
  const [stage, setStage] = useState<string>("");
  const [chars, setChars] = useState(0);
  const [busy, setBusy] = useState(false);

  const [conversationId, setConversationId] = useState<number | null>(null);
  const [title, setTitle] = useState("");
  const [sidebarOpen, setSidebarOpen] = useState(false);
  // Bumped after every solve so the sidebar re-reads the thread list.
  const [reloadToken, setReloadToken] = useState(0);

  const abortRef = useRef<AbortController | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [entries, stage]);

  // A problem can arrive from /scan as `?problem=`. It is placed in the
  // composer, NOT submitted: the whole point of the scan flow is that the
  // student confirms the transcription, and auto-solving on arrival would
  // undo that one step later.
  //
  // Read from window rather than `useSearchParams`, which would force this
  // page under a Suspense boundary to read one optional string.
  useEffect(() => {
    const scanned = new URLSearchParams(window.location.search).get("problem");
    if (scanned) setInput(scanned);
  }, []);

  const cancel = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    setBusy(false);
    setStage("");
  }, []);

  // ── history navigation ────────────────────────────────────────────────

  const newChat = useCallback(() => {
    cancel();
    setEntries([]);
    setConversationId(null);
    setTitle("");
    setInput("");
  }, [cancel]);

  const openConversation = useCallback(
    async (id: number) => {
      cancel();
      try {
        const conversation = await getConversation(id);
        setEntries(entriesFrom(conversation.turns));
        setConversationId(conversation.id);
        setTitle(conversation.title);
      } catch {
        setEntries([
          {
            id: Date.now(),
            problem: "",
            tier,
            error: "That chat could not be loaded.",
          },
        ]);
      }
    },
    [cancel, tier],
  );

  // A search hit is a turn, not a thread — open the thread it belongs to.
  // Search returns summaries without the solution body, so there is nothing
  // to render from the hit itself.
  const openTurn = useCallback(
    (turn: TurnSummary | TurnDetail) => void openConversation(turn.conversation_id),
    [openConversation],
  );

  // ── bookmarks and notes ───────────────────────────────────────────────

  const bookmark = useCallback(async (turnId: number, on: boolean) => {
    await setBookmark(turnId, on);
    setEntries((prev) =>
      prev.map((e) => (e.turnId === turnId ? { ...e, bookmarked: on } : e)),
    );
    setReloadToken((n) => n + 1);
  }, []);

  const annotate = useCallback(async (turnId: number, text: string) => {
    await setNote(turnId, text);
    setEntries((prev) =>
      prev.map((e) => (e.turnId === turnId ? { ...e, note: text } : e)),
    );
    setReloadToken((n) => n + 1);
  }, []);

  // ── solving ───────────────────────────────────────────────────────────

  const submit = useCallback(
    async (problem: string) => {
      const text = problem.trim();
      if (!text || busy) return;

      const id = Date.now();
      setEntries((prev) => [...prev, { id, problem: text, tier }]);
      setInput("");
      setBusy(true);
      setChars(0);
      setStage("Sending…");

      const controller = new AbortController();
      abortRef.current = controller;

      const fail = (message: string, hint?: string) =>
        setEntries((prev) =>
          prev.map((e) => (e.id === id ? { ...e, error: message, hint } : e)),
        );

      try {
        for await (const event of streamSolve(text, {
          tier,
          conversationId,
          signal: controller.signal,
        })) {
          if (event.type === "stage") {
            setStage(event.message);
          } else if (event.type === "delta") {
            // Count characters rather than render them — the payload is JSON.
            setChars((n) => n + event.text.length);
          } else if (event.type === "result") {
            // Adopt the thread the backend created for the first solve, so
            // the next question continues it instead of starting another.
            if (event.conversation_id != null) {
              setConversationId(event.conversation_id);
            }
            setEntries((prev) =>
              prev.map((e) =>
                e.id === id
                  ? {
                      ...e,
                      turnId: event.turn_id ?? null,
                      bookmarked: false,
                      note: "",
                      result: {
                        verified: event.verified,
                        solution: event.solution,
                        verdict: event.verdict,
                        total_ms: event.total_ms,
                      },
                    }
                  : e,
              ),
            );
            setReloadToken((n) => n + 1);
          } else if (event.type === "error") {
            fail(event.message);
          }
        }
      } catch (error) {
        if (error instanceof NetworkError) {
          fail(
            "Cannot reach the backend.",
            IS_LOCAL_API
              ? "Start it with:  cd backend && uvicorn app.main:app --reload"
              : "The API server is not responding. It may be starting up — wait a moment and try again.",
          );
        } else if (error instanceof ApiError) {
          fail(
            error.status === 429
              ? "Rate limited by the model provider."
              : `API error ${error.status}: ${error.message}`,
            error.status === 429
              ? "The free tier allows a limited number of requests. Wait a moment, or set LLM_PROVIDER=mock in .env."
              : undefined,
          );
        } else if ((error as Error)?.name !== "AbortError") {
          fail(String(error));
        }
      } finally {
        setBusy(false);
        setStage("");
        abortRef.current = null;
      }
    },
    [busy, tier, conversationId],
  );

  return (
    <div className="lg:pl-[17rem]">
      <Sidebar
        activeId={conversationId}
        onOpenConversation={(id) => void openConversation(id)}
        onOpenTurn={openTurn}
        onNewChat={newChat}
        open={sidebarOpen}
        onClose={() => setSidebarOpen(false)}
        reloadToken={reloadToken}
      />

      <main className="mx-auto flex min-h-dvh max-w-3xl flex-col px-4 sm:px-6">
        <header className="flex items-start gap-3 pt-10 pb-6">
          <button
            onClick={() => setSidebarOpen(true)}
            aria-label="Open history"
            className="mt-1 rounded-lg border border-line px-2.5 py-1.5 text-muted transition-colors hover:text-paper lg:hidden"
          >
            ☰
          </button>
          <div className="min-w-0 flex-1">
            <h1 className="font-display text-3xl font-bold tracking-tight">MathBot</h1>
            <p className="mt-1.5 text-sm leading-relaxed text-muted">
              Every answer is recomputed by a computer algebra system before you
              see it. If the check fails, you are told.
            </p>
            {title && (
              <p className="mt-2 truncate font-mono text-[11px] text-muted/70">
                {title}
              </p>
            )}
          </div>
        </header>

        {/* ── conversation ───────────────────────────────────────────── */}
        <div className="flex-1 space-y-6 pb-4">
          {entries.length === 0 && !busy && (
            <div className="rounded-2xl border border-dashed border-line p-5">
              <p className="mb-3 font-mono text-[11px] uppercase tracking-[0.16em] text-muted">
                Try one of these
              </p>
              <div className="flex flex-col gap-2">
                {EXAMPLES.map((example) => (
                  <button
                    key={example}
                    onClick={() => void submit(example)}
                    className="rounded-lg border border-line px-3.5 py-2.5 text-left text-sm text-muted transition-colors hover:border-accent hover:text-paper"
                  >
                    {example}
                  </button>
                ))}
              </div>
            </div>
          )}

          {entries.map((entry) => (
            <div key={entry.id} className="space-y-3">
              {/* the question */}
              {entry.problem && (
                <div className="flex justify-end">
                  <div className="max-w-[85%] rounded-2xl rounded-br-sm bg-accent px-4 py-2.5 text-[15px] text-paper">
                    {entry.problem}
                  </div>
                </div>
              )}

              {entry.error && (
                <div className="rounded-xl border border-wrong/40 bg-wrong/5 p-4">
                  <p className="text-sm text-wrong">{entry.error}</p>
                  {entry.hint && (
                    <pre className="mt-2.5 overflow-x-auto rounded-lg bg-ink p-3 font-mono text-[11px] leading-relaxed text-muted">
                      {entry.hint}
                    </pre>
                  )}
                </div>
              )}

              {entry.result && (
                <SolutionCard
                  solution={entry.result.solution}
                  verdict={entry.result.verdict}
                  verified={entry.result.verified}
                  totalMs={entry.result.total_ms}
                  problem={entry.problem}
                  turnId={entry.turnId}
                  bookmarked={entry.bookmarked}
                  note={entry.note}
                  onBookmark={bookmark}
                  onNote={annotate}
                />
              )}
            </div>
          ))}

          {/* ── live progress ────────────────────────────────────────── */}
          {busy && (
            <div
              className="flex items-center gap-3 rounded-2xl border border-line bg-slate px-4 py-3.5"
              aria-live="polite"
            >
              <span className="relative flex h-2 w-2 shrink-0">
                <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-accent opacity-60" />
                <span className="relative inline-flex h-2 w-2 rounded-full bg-accent" />
              </span>
              <span className="flex-1 text-sm text-muted">
                {stage || "Working…"}
                {chars > 0 && (
                  <span className="ml-2 font-mono text-[11px] text-muted/70">
                    {chars.toLocaleString()} chars
                  </span>
                )}
              </span>
              <button
                onClick={cancel}
                className="rounded-lg border border-line px-2.5 py-1 text-[11px] text-muted transition-colors hover:border-wrong hover:text-wrong"
              >
                Cancel
              </button>
            </div>
          )}

          <div ref={bottomRef} />
        </div>

        {/* ── composer ───────────────────────────────────────────────── */}
        <div className="sticky bottom-0 -mx-4 border-t border-line bg-ink/95 px-4 pt-3 pb-5 backdrop-blur sm:-mx-6 sm:px-6">
          <div className="mb-2.5 flex flex-wrap gap-1.5">
            {TIERS.map((option) => (
              <button
                key={option.value}
                onClick={() => setTier(option.value)}
                title={option.blurb}
                className={`rounded-full border px-3 py-1 text-[11px] transition-colors ${
                  tier === option.value
                    ? "border-accent bg-accent/10 text-accent"
                    : "border-line text-muted hover:text-paper"
                }`}
              >
                {option.label}
              </button>
            ))}
            <span className="self-center pl-1 font-mono text-[10px] text-muted/60">
              {TIERS.find((t) => t.value === tier)?.blurb}
            </span>
            {conversationId != null && (
              <span className="ml-auto self-center font-mono text-[10px] text-muted/60">
                continuing this chat
              </span>
            )}
          </div>

          <form
            onSubmit={(e) => {
              e.preventDefault();
              void submit(input);
            }}
            className="flex items-end gap-2"
          >
            <textarea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                // Enter sends, Shift+Enter makes a new line. Multi-line matters
                // here — people paste problems with working across several lines.
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  void submit(input);
                }
              }}
              rows={1}
              placeholder="Type a problem…  (Shift+Enter for a new line)"
              disabled={busy}
              className="max-h-40 min-h-[46px] flex-1 resize-y rounded-xl border border-line bg-slate px-3.5 py-3 text-[15px] text-paper placeholder:text-muted/60 focus:border-accent focus:outline-none disabled:opacity-50"
            />
            <button
              type="submit"
              disabled={busy || !input.trim()}
              className="h-[46px] shrink-0 rounded-xl bg-accent px-5 text-sm font-semibold text-paper transition-colors hover:bg-accent-soft disabled:opacity-40"
            >
              Solve
            </button>
          </form>
        </div>
      </main>
    </div>
  );
}
