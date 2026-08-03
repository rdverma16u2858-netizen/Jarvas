/**
 * History API client — conversations, search and bookmarks.
 * ═══════════════════════════════════════════════════════════════════════
 *
 * Mirrors backend/app/api/routes/conversations.py. Every function here maps
 * to exactly one endpoint, so a change on either side has one obvious place
 * to land on the other.
 *
 * WHY TurnDetail EXTENDS TurnSummary
 *   The backend returns summaries in lists (search results, sidebar rows) and
 *   full detail where a solution card has to be drawn (a thread, the
 *   bookmarks page). The list rows would otherwise carry a whole derivation
 *   each, which is dead weight in a sidebar.
 *
 *   `solution` and `verdict` arrive as plain JSON objects — the backend stores
 *   them as JSON columns. They are cast to the Solution/Verdict types on the
 *   way out of `turnResult` below rather than being validated, because they
 *   were written by the backend from those very models.
 */

import { apiFetch } from "./api";
import type { Solution, SolveResult, Verdict } from "./solve";

// ── Response shapes ───────────────────────────────────────────────────────

export interface TurnSummary {
  id: number;
  conversation_id: number;
  problem: string;
  final_answer: string;
  topic: string;
  difficulty: string;
  verified: boolean;
  verdict_kind: string;
  bookmarked: boolean;
  note: string;
  created_at: string;
}

export interface TurnDetail extends TurnSummary {
  solution: Solution;
  verdict: Verdict;
  model: string;
  tier: string;
  latency_ms: number;
}

export interface ConversationSummary {
  id: number;
  title: string;
  turn_count: number;
  archived: boolean;
  last_turn_at: string | null;
  created_at: string;
}

export interface ConversationDetail extends ConversationSummary {
  turns: TurnDetail[];
}

/** Rebuild the shape SolutionCard wants from a stored turn. */
export function turnResult(turn: TurnDetail): SolveResult {
  return {
    verified: turn.verified,
    solution: turn.solution,
    verdict: turn.verdict,
    total_ms: turn.latency_ms,
  };
}

// ── Conversations ─────────────────────────────────────────────────────────

export function listConversations(
  { includeArchived = false }: { includeArchived?: boolean } = {},
): Promise<ConversationSummary[]> {
  return apiFetch<ConversationSummary[]>(
    `/conversations?include_archived=${includeArchived}`,
  );
}

export function getConversation(id: number): Promise<ConversationDetail> {
  return apiFetch<ConversationDetail>(`/conversations/${id}`);
}

export function renameConversation(
  id: number,
  title: string,
): Promise<ConversationSummary> {
  return apiFetch<ConversationSummary>(`/conversations/${id}`, {
    method: "PATCH",
    body: JSON.stringify({ title }),
  });
}

export function archiveConversation(
  id: number,
  archived: boolean,
): Promise<ConversationSummary> {
  return apiFetch<ConversationSummary>(`/conversations/${id}`, {
    method: "PATCH",
    body: JSON.stringify({ archived }),
  });
}

/** Permanent — takes every turn in the thread with it. */
export function deleteConversation(id: number): Promise<void> {
  return apiFetch<void>(`/conversations/${id}`, { method: "DELETE" });
}

// ── Search and bookmarks ──────────────────────────────────────────────────

export function searchHistory(
  query: string,
  { verifiedOnly = false, bookmarkedOnly = false } = {},
): Promise<TurnSummary[]> {
  // encodeURIComponent, not template interpolation: a query containing "&"
  // or "#" would otherwise be read as more query parameters.
  const params = new URLSearchParams({ q: query });
  if (verifiedOnly) params.set("verified_only", "true");
  if (bookmarkedOnly) params.set("bookmarked_only", "true");
  return apiFetch<TurnSummary[]>(`/conversations/search?${params}`);
}

export function listBookmarks(): Promise<TurnDetail[]> {
  return apiFetch<TurnDetail[]>("/conversations/bookmarks");
}

export function setBookmark(
  turnId: number,
  bookmarked: boolean,
): Promise<TurnSummary> {
  return apiFetch<TurnSummary>(`/conversations/turns/${turnId}/bookmark`, {
    method: "POST",
    body: JSON.stringify({ bookmarked }),
  });
}

export function setNote(turnId: number, note: string): Promise<TurnSummary> {
  return apiFetch<TurnSummary>(`/conversations/turns/${turnId}/note`, {
    method: "PATCH",
    body: JSON.stringify({ note }),
  });
}
