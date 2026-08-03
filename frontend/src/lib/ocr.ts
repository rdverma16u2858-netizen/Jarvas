/**
 * OCR API client — read a problem out of an image.
 * ═══════════════════════════════════════════════════════════════════════
 *
 * Mirrors backend/app/api/routes/ocr.py.
 *
 * WHY THIS DOES NOT GO THROUGH apiFetch
 *   `apiFetch` sets Content-Type: application/json on every request. For a
 *   multipart upload the browser must set that header ITSELF, because it has
 *   to append the multipart boundary — overriding it produces a body the
 *   server cannot parse, with an error that points at the body rather than
 *   the header.
 *
 * WHY THE RESULT IS NEVER SOLVED AUTOMATICALLY
 *   A misread problem is still a well-formed problem. Sending it straight to
 *   /solve would produce a verified, confident answer to a question the
 *   student never asked, and nothing downstream could tell. The transcription
 *   is shown, and the student confirms or corrects it.
 */

import { API_BASE_URL, ApiError, NetworkError, UnauthorizedError } from "./api";
import { authHeaders, clearToken } from "./auth";

export type Legibility = "clear" | "partial" | "unreadable";

export interface Extraction {
  problem: string;
  plain: string;
  topic: string;

  legibility: Legibility;
  /** Specific doubts, each naming where and what. More useful than a score. */
  uncertain: string[];
  /** True unless the reading was clean AND nothing was flagged. */
  needs_checking: boolean;
  /** False when nothing usable came out — ask for a better photograph. */
  usable: boolean;

  contains_working: boolean;
  working: string;

  notes: string;
  model: string;
  total_ms: number;
}

export interface OcrLimits {
  max_bytes: number;
  max_megabytes: number;
  allowed_types: string[];
}

export function getOcrLimits(): Promise<OcrLimits> {
  return fetch(`${API_BASE_URL}/ocr/limits`, { cache: "no-store" }).then((r) =>
    r.json(),
  );
}

export async function extractFromImage(
  file: File,
  { hint = "", signal }: { hint?: string; signal?: AbortSignal } = {},
): Promise<Extraction> {
  const form = new FormData();
  form.append("image", file);
  form.append("hint", hint);

  let response: Response;
  try {
    // No Content-Type header on purpose — see the module docstring. The auth
    // header is fine to set: it is the boundary that must be left alone.
    response = await fetch(`${API_BASE_URL}/ocr`, {
      method: "POST",
      headers: authHeaders(),
      body: form,
      signal,
    });
  } catch (cause) {
    if ((cause as Error)?.name === "AbortError") throw cause;
    throw new NetworkError(
      `Cannot reach the API at ${API_BASE_URL}. Is the backend running?`,
    );
  }

  if (response.status === 401) {
    clearToken();
    throw new UnauthorizedError();
  }

  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      if (typeof body?.detail === "string") message = body.detail;
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(message, response.status);
  }

  return (await response.json()) as Extraction;
}

// ── display ───────────────────────────────────────────────────────────────

export const LEGIBILITY_STYLE: Record<
  Legibility,
  { label: string; chip: string; blurb: string }
> = {
  clear: {
    label: "read cleanly",
    chip: "border-verified/40 bg-verified/10 text-verified",
    blurb: "Every symbol was legible. Still worth a glance before solving.",
  },
  partial: {
    label: "check this",
    chip: "border-unverified/40 bg-unverified/10 text-unverified",
    blurb:
      "Some symbols had to be guessed. Fix anything wrong below before solving.",
  },
  unreadable: {
    label: "could not read",
    chip: "border-wrong/40 bg-wrong/10 text-wrong",
    blurb:
      "Not enough could be made out. A closer, better-lit photograph of one problem usually fixes it.",
  },
};
