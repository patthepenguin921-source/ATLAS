"use client";

import { getSupabase } from "./supabase";

const BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "/api/backend";

/** Turns a failed response's body into something readable in a chat bubble.
 *  The backend's `SupabaseError`/`R2Error`/`LLMError` handlers (see
 *  `app.main`) all return `{"detail": {"source", "status", "error"}}` --
 *  `error` for the "llm" source is the raw upstream provider payload (e.g.
 *  Groq's rate-limit JSON), which is not something a student should see
 *  verbatim. Falls back to the raw response text for anything that doesn't
 *  match this shape (validation errors, proxy/gateway HTML, etc). */
function friendlyErrorMessage(status: number, rawText: string): string {
  let detail: any = null;
  try {
    detail = JSON.parse(rawText)?.detail;
  } catch {
    /* not JSON -- fall through to the raw text below */
  }
  if (detail?.source === "llm") {
    if (status === 429) {
      const wait = String(detail.error ?? "").match(/try again in ([\d.]+)\s*s/i);
      return wait
        ? `Atlas is getting a lot of requests right now -- try again in about ${Math.ceil(Number(wait[1]))}s.`
        : "Atlas is getting a lot of requests right now. Please try again in a few seconds.";
    }
    return "Atlas's reasoning engine is temporarily unavailable. Please try again shortly.";
  }
  if (detail?.source === "supabase" || detail?.source === "r2") {
    return status === 429
      ? "Atlas is temporarily overloaded. Please try again in a moment."
      : "Atlas hit a temporary error reaching your data. Please try again.";
  }
  return rawText || `Request failed (${status}).`;
}

async function authHeader(): Promise<Record<string, string>> {
  try {
    const { data } = await getSupabase().auth.getSession();
    const token = data.session?.access_token;
    if (token) return { Authorization: `Bearer ${token}` };
  } catch {
    /* not signed in */
  }
  return {};
}

export async function api<T = any>(
  path: string,
  opts: RequestInit & { timeoutMs?: number } = {}
): Promise<T> {
  const { timeoutMs, ...rest } = opts;
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(await authHeader()),
    ...((rest.headers as Record<string, string>) ?? {}),
  };
  // Without this, a request that never gets a response (a dropped connection,
  // a proxy that silently stalls instead of returning an error) leaves the
  // caller's fetch promise pending forever — the UI just looks broken with no
  // way to tell "still working" from "never coming back." A bounded timeout
  // guarantees the caller eventually sees a real, catchable error.
  const controller = new AbortController();
  const timer = timeoutMs
    ? setTimeout(() => controller.abort(), timeoutMs)
    : undefined;
  let res: Response;
  try {
    res = await fetch(`${BASE}/api/v1${path}`, { ...rest, headers, signal: controller.signal });
  } catch (err: any) {
    if (err?.name === "AbortError") {
      throw new Error("The request took too long and timed out. Please try again.");
    }
    throw err;
  } finally {
    if (timer) clearTimeout(timer);
  }
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText);
    throw new Error(friendlyErrorMessage(res.status, text));
  }
  if (res.status === 204) return undefined as T;
  return res.json();
}

export const apiGet = <T = any>(p: string) => api<T>(p);
export const apiPost = <T = any>(p: string, body?: unknown, timeoutMs?: number) =>
  api<T>(p, { method: "POST", body: body ? JSON.stringify(body) : undefined, timeoutMs });
export const apiPatch = <T = any>(p: string, body: unknown) =>
  api<T>(p, { method: "PATCH", body: JSON.stringify(body) });
export const apiPut = <T = any>(p: string, body: unknown) =>
  api<T>(p, { method: "PUT", body: JSON.stringify(body) });
export const apiDelete = (p: string) => api(p, { method: "DELETE" });

/** Multipart upload (for documents) with auth header, no JSON content-type.
 *  Uses XMLHttpRequest instead of fetch so `onProgress` can report how much
 *  of the file has actually reached the server — fetch exposes no upload
 *  progress event. That percentage covers only the network transfer; the
 *  server's extraction/embedding work afterward has no progress to report. */
export async function apiUpload<T = any>(
  path: string, form: FormData, onProgress?: (pct: number) => void
): Promise<T> {
  const headers = await authHeader();
  return new Promise<T>((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${BASE}/api/v1${path}`);
    for (const [k, v] of Object.entries(headers)) xhr.setRequestHeader(k, v);
    if (onProgress) {
      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable) onProgress(Math.round((e.loaded / e.total) * 100));
      };
    }
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          resolve(xhr.responseText ? JSON.parse(xhr.responseText) : (undefined as T));
        } catch {
          resolve(undefined as T);
        }
      } else {
        reject(new Error(friendlyErrorMessage(xhr.status, xhr.responseText)));
      }
    };
    xhr.onerror = () => reject(new Error("Failed to fetch"));
    xhr.send(form);
  });
}

export { BASE as API_BASE };
