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

/** Longest edge, in pixels, that a photograph is reduced to before upload. */
const MAX_EDGE = 1600;

/** JPEG quality for the reduced image. */
const QUALITY = 0.85;

/**
 * Shrink a photograph before sending it.
 *
 * A phone camera produces 3-6 MB at 4000px across. None of that resolution
 * helps read a line of handwriting, and all of it hurts: minutes of upload on
 * mobile data, a base64 payload a third larger again, and a free-tier
 * container holding the whole thing in memory while it forwards it.
 *
 * 1600px on the long edge is comfortably enough to read handwritten
 * mathematics and typically lands under 400 KB — often a tenfold reduction.
 *
 * Falls back to the original file on any failure. A slow upload is worse than
 * a fast one; a broken one is worse than both.
 */
async function downscale(file: File): Promise<File> {
  // Nothing to gain on an image that is already small.
  if (file.size < 400_000) return file;

  try {
    const bitmap = await createImageBitmap(file);
    const scale = Math.min(1, MAX_EDGE / Math.max(bitmap.width, bitmap.height));
    if (scale === 1) {
      bitmap.close();
      return file;
    }

    const canvas = document.createElement("canvas");
    canvas.width = Math.round(bitmap.width * scale);
    canvas.height = Math.round(bitmap.height * scale);

    const context = canvas.getContext("2d");
    if (!context) return file;
    context.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
    bitmap.close();

    const blob = await new Promise<Blob | null>((resolve) =>
      canvas.toBlob(resolve, "image/jpeg", QUALITY),
    );
    if (!blob || blob.size >= file.size) return file;

    return new File([blob], "scan.jpg", { type: "image/jpeg" });
  } catch {
    // Unsupported format, out of memory on an old phone, a canvas the browser
    // refuses to read back — none of these should stop the upload.
    return file;
  }
}

export async function extractFromImage(
  file: File,
  { hint = "", signal }: { hint?: string; signal?: AbortSignal } = {},
): Promise<Extraction> {
  const upload = await downscale(file);

  const form = new FormData();
  form.append("image", upload);
  form.append("hint", hint);

  // The same cold-start tolerance every other request gets. This call does not
  // go through apiFetch — multipart needs the browser to set its own
  // Content-Type boundary — and it was the ONE request left without a retry,
  // so scanning was the one feature that broke against a sleeping backend
  // while the rest of the app waited politely.
  const WAKE_RETRIES = 10;
  const WAKE_DELAY_MS = 4000;

  let response: Response | null = null;

  for (let attempt = 0; ; attempt++) {
    try {
      // No Content-Type header on purpose — see the module docstring. The auth
      // header is fine to set: it is the boundary that must be left alone.
      response = await fetch(`${API_BASE_URL}/ocr`, {
        method: "POST",
        headers: authHeaders(),
        body: form,
        signal,
      });
      break;
    } catch (cause) {
      if ((cause as Error)?.name === "AbortError") throw cause;

      // Nothing reached the server, so re-sending cannot duplicate work.
      if (attempt < WAKE_RETRIES) {
        await new Promise((resolve) => setTimeout(resolve, WAKE_DELAY_MS));
        continue;
      }

      throw new NetworkError(
        `Cannot reach the API at ${API_BASE_URL}. Is the backend running?`,
      );
    }
  }

  // The loop either assigns a response or throws; TypeScript cannot see that
  // through the retry.
  if (response === null) {
    throw new NetworkError(`Cannot reach the API at ${API_BASE_URL}.`);
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
