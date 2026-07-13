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
  opts: RequestInit = {}
): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(await authHeader()),
    ...((opts.headers as Record<string, string>) ?? {}),
  };
  const res = await fetch(`${BASE}/api/v1${path}`, { ...opts, headers });
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText);
    throw new Error(`${res.status}: ${text}`);
  }
  if (res.status === 204) return undefined as T;
  return res.json();
}

export const apiGet = <T = any>(p: string) => api<T>(p);
export const apiPost = <T = any>(p: string, body?: unknown) =>
  api<T>(p, { method: "POST", body: body ? JSON.stringify(body) : undefined });
export const apiPatch = <T = any>(p: string, body: unknown) =>
  api<T>(p, { method: "PATCH", body: JSON.stringify(body) });
export const apiDelete = (p: string) => api(p, { method: "DELETE" });

/** Multipart upload (for documents) with auth header, no JSON content-type. */
export async function apiUpload<T = any>(path: string, form: FormData): Promise<T> {
  const res = await fetch(`${BASE}/api/v1${path}`, {
    method: "POST",
    headers: { ...(await authHeader()) },
    body: form,
  });
  if (!res.ok) throw new Error(`${res.status}: ${await res.text()}`);
  return res.json();
}

export { BASE as API_BASE };
