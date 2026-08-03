/**
 * Solve API client, including the SSE stream.
 * ═══════════════════════════════════════════════════════════════════════
 *
 * WHY fetch + ReadableStream INSTEAD OF EventSource
 *   `EventSource` is the obvious tool for SSE and cannot be used here: it
 *   only issues GET requests. The problem text belongs in a POST body — a
 *   long LaTeX problem does not fit sensibly in a query string, and putting
 *   it there leaks the content into server logs and browser history.
 *
 *   Reading the response body as a stream gives the same result with POST,
 *   at the cost of parsing the SSE framing by hand. That parsing is the
 *   `readStream` function below and is about fifteen lines.
 */

import { API_BASE_URL, ApiError, NetworkError, UnauthorizedError } from "./api";
import { authHeaders, clearToken } from "./auth";

// ── Mirrors of the backend schema (app/math/schema.py) ────────────────────

export type Tier = "fast" | "balanced" | "deep";

export type VerdictKind = "verified" | "refuted" | "unverifiable" | "error";

export interface Step {
  number: number;
  action: string;
  expression: string;
  justification: string;
}

export interface VerificationClaim {
  kind: string;
  expression: string;
  variable: string;
  lower: string;
  upper: string;
  result: string;
  roots: string[];
}

export interface Solution {
  topic: string;
  steps: Step[];
  formulas_used: string[];
  final_answer: string;
  answer_latex: string;
  common_mistakes: string[];
  alternative_method: string;
  concepts: string[];
  practice_question: string;
  difficulty: string;
  time_minutes: number;
  verification: VerificationClaim;
}

export interface Verdict {
  kind: VerdictKind;
  detail: string;
  expected: string;
  claimed: string;
  checks: string[];
}

export interface SolveResult {
  verified: boolean;
  solution: Solution;
  verdict: Verdict;
  total_ms: number;
}

// ── Stream events ─────────────────────────────────────────────────────────

export type SolveEvent =
  | { type: "stage"; stage: "solving" | "verifying"; message: string }
  | { type: "delta"; text: string }
  | {
      type: "result";
      verified: boolean;
      solution: Solution;
      verdict: Verdict;
      total_ms: number;
      // Present when the turn was saved. The ids ride along with the result
      // so the UI can offer "bookmark this" immediately, without a second
      // request to find out what it just created.
      conversation_id?: number;
      turn_id?: number;
    }
  | { type: "error"; message: string }
  | { type: "done" };

/**
 * POST to /solve/stream and yield each event as it arrives.
 *
 * An async generator rather than a callback: the caller writes a plain
 * `for await` loop, and cancelling is just breaking out of it.
 *
 * @param conversationId Continue an existing thread, so a follow-up question
 *                       ("now do it from 0 to infinity") has an antecedent.
 *                       Omit to start a new one — the backend creates it.
 * @param save           False solves without recording anything in history.
 * @param signal         AbortSignal — lets the UI cancel a solve in flight.
 *                       Worth having when a deep-tier request can run 35
 *                       seconds and the student changes their mind.
 */
export async function* streamSolve(
  problem: string,
  {
    tier = "balanced",
    conversationId,
    save = true,
    signal,
  }: {
    tier?: Tier;
    conversationId?: number | null;
    save?: boolean;
    signal?: AbortSignal;
  } = {},
): AsyncGenerator<SolveEvent> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}/solve/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders() },
      body: JSON.stringify({
        problem,
        tier,
        save,
        // null and undefined both mean "start a new thread"; the backend
        // field is Optional[int], so null is the value it expects.
        conversation_id: conversationId ?? null,
      }),
      signal,
    });
  } catch (cause) {
    if ((cause as Error)?.name === "AbortError") return;
    throw new NetworkError(
      `Cannot reach the API at ${API_BASE_URL}. Is the backend running?`,
    );
  }

  // A failure BEFORE streaming begins still arrives as a normal HTTP error,
  // so it is handled here. Failures DURING the stream arrive as an `error`
  // event instead — the status line is already sent by then and cannot change.
  if (response.status === 401) {
    clearToken();
    throw new UnauthorizedError();
  }

  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      if (body?.detail) message = String(body.detail);
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(message, response.status);
  }

  if (!response.body) throw new NetworkError("The response had no body to stream.");

  yield* readStream(response.body);
}

/** Parse the SSE wire format: `data: {...}` lines separated by blank lines. */
async function* readStream(body: ReadableStream<Uint8Array>): AsyncGenerator<SolveEvent> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });

      // Events are separated by a blank line. Anything after the last
      // separator is a partial event and stays in the buffer until the rest
      // of it arrives — splitting on newline alone would corrupt any JSON
      // payload that happens to straddle a chunk boundary.
      const chunks = buffer.split("\n\n");
      buffer = chunks.pop() ?? "";

      for (const chunk of chunks) {
        const line = chunk.split("\n").find((l) => l.startsWith("data:"));
        if (!line) continue;
        try {
          yield JSON.parse(line.slice(5).trim()) as SolveEvent;
        } catch {
          /* keep-alive or malformed frame — skip it rather than fail */
        }
      }
    }
  } finally {
    // Runs on `break` out of the caller's for-await too, so cancelling the
    // UI releases the connection instead of leaking it.
    reader.releaseLock();
  }
}
