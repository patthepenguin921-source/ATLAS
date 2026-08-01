"use client";

import { getSupabase } from "./supabase";

const BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "/api/backend";

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
    throw new Error(`${res.status}: ${text}`);
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
        reject(new Error(`${xhr.status}: ${xhr.responseText}`));
      }
    };
    xhr.onerror = () => reject(new Error("Failed to fetch"));
    xhr.send(form);
  });
}

export { BASE as API_BASE };
