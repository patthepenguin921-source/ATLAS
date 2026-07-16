"use client";

import { useEffect, useState } from "react";
import { AppShell } from "@/components/AppShell";
import { Empty, Loading, Badge, Modal, Section } from "@/components/ui";
import { apiGet, apiPost, apiDelete } from "@/lib/api";

interface Integration {
  provider: string;
  display_name?: string | null;
  status: "idle" | "running" | "success" | "error";
  last_synced_at?: string | null;
  last_error?: string | null;
  enabled: boolean;
  config?: { auth_mode?: "password" | "cookie" };
}

interface SyncResult {
  status: string;
  courses?: number;
  assignments?: number;
  grades?: number;
  errors?: string[];
  detail?: string;
}

interface ProbeResult {
  requested_url: string;
  final_url: string;
  status_code: number;
  page_title: string | null;
  has_login_form: boolean;
  login_type: "legacy" | "cas" | null;
  browser_fallback_available: boolean;
  forms: { id: string | null; action: string | null; input_names: string[] }[];
  html_snippet: string;
}

const STATUS_TONE: Record<string, "good" | "warn" | "bad" | "default"> = {
  success: "good",
  running: "warn",
  error: "bad",
  idle: "default",
};

export default function IntegrationsPage() {
  const [integrations, setIntegrations] = useState<Integration[] | null>(null);
  const [connectOpen, setConnectOpen] = useState(false);
  const [connecting, setConnecting] = useState(false);
  const [syncingProvider, setSyncingProvider] = useState<string | null>(null);
  const [lastResult, setLastResult] = useState<SyncResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [mode, setMode] = useState<"password" | "cookie">("password");
  const [form, setForm] = useState({ base_url: "", username: "", password: "", cookie: "" });
  const [probing, setProbing] = useState(false);
  const [probeResult, setProbeResult] = useState<ProbeResult | null>(null);
  const [probeError, setProbeError] = useState<string | null>(null);
  const [showSnippet, setShowSnippet] = useState(false);

  async function load() {
    setIntegrations(await apiGet<Integration[]>("/integrations"));
  }
  useEffect(() => {
    load();
  }, []);

  const powerschool = integrations?.find((i) => i.provider === "powerschool");

  function closeConnectModal() {
    setConnectOpen(false);
    setProbeResult(null);
    setProbeError(null);
    setShowSnippet(false);
  }

  async function testUrl() {
    if (!form.base_url.trim()) return;
    setProbing(true);
    setProbeError(null);
    setProbeResult(null);
    setShowSnippet(false);
    try {
      const result = await apiGet<ProbeResult>(
        `/integrations/powerschool/probe?base_url=${encodeURIComponent(form.base_url)}`
      );
      setProbeResult(result);
    } catch (err: any) {
      setProbeError(err.message ?? "Could not reach that URL.");
    } finally {
      setProbing(false);
    }
  }

  async function connect(e: React.FormEvent) {
    e.preventDefault();
    setConnecting(true);
    setError(null);
    setLastResult(null);
    try {
      const result =
        mode === "cookie"
          ? await apiPost<SyncResult>("/integrations/powerschool/connect-session", {
              base_url: form.base_url,
              cookie: form.cookie,
            })
          : await apiPost<SyncResult>("/integrations/powerschool/connect", {
              base_url: form.base_url,
              username: form.username,
              password: form.password,
            });
      setLastResult(result);
      closeConnectModal();
      setForm({ base_url: "", username: "", password: "", cookie: "" });
      await load();
    } catch (err: any) {
      setError(err.message ?? "Connection failed.");
    } finally {
      setConnecting(false);
    }
  }

  async function sync(provider: string) {
    setSyncingProvider(provider);
    setError(null);
    setLastResult(null);
    try {
      const result = await apiPost<SyncResult>(`/integrations/${provider}/sync`);
      setLastResult(result);
      await load();
    } catch (err: any) {
      setError(err.message ?? "Sync failed.");
    } finally {
      setSyncingProvider(null);
    }
  }

  async function disconnect(provider: string) {
    if (!confirm("Disconnect PowerSchool? This removes your saved login; imported grades stay.")) return;
    await apiDelete(`/integrations/${provider}`);
    setLastResult(null);
    await load();
  }

  return (
    <AppShell title="Integrations" subtitle="Connect your school's systems so Atlas stays current on its own">
      {integrations === null ? (
        <Loading />
      ) : (
        <Section title="Grades & assignments">
          <div className="card">
            <div className="flex items-center justify-between">
              <div>
                <div className="font-medium flex items-center gap-2">
                  PowerSchool
                  {powerschool && <Badge tone={STATUS_TONE[powerschool.status]}>{powerschool.status}</Badge>}
                  {powerschool?.config?.auth_mode === "cookie" && <Badge>Session cookie</Badge>}
                </div>
                <div className="text-sm text-atlas-muted mt-1">
                  {powerschool
                    ? powerschool.last_synced_at
                      ? `Last synced ${new Date(powerschool.last_synced_at).toLocaleString()}`
                      : "Connected — not yet synced"
                    : "Auto-import courses, current grades, and per-assignment scores from your PowerSchool portal."}
                </div>
                {powerschool?.last_error && (
                  <div className="text-sm text-atlas-bad mt-1">{powerschool.last_error}</div>
                )}
              </div>
              <div className="flex items-center gap-2 shrink-0">
                {powerschool ? (
                  <>
                    <button
                      className="btn-ghost"
                      disabled={syncingProvider === "powerschool"}
                      onClick={() => sync("powerschool")}
                    >
                      {syncingProvider === "powerschool" ? "Syncing…" : "Sync now"}
                    </button>
                    <button className="btn-ghost" onClick={() => disconnect("powerschool")}>
                      Disconnect
                    </button>
                  </>
                ) : (
                  <button className="btn-primary" onClick={() => setConnectOpen(true)}>
                    Connect
                  </button>
                )}
              </div>
            </div>
          </div>

          {lastResult && (
            <div className="card mt-4 text-sm">
              {lastResult.status === "success" ? (
                <>
                  <div className="font-medium text-atlas-good mb-1">Sync complete</div>
                  <div className="text-atlas-muted">
                    {lastResult.courses ?? 0} courses · {lastResult.assignments ?? 0} assignments ·{" "}
                    {lastResult.grades ?? 0} grades imported
                  </div>
                  {lastResult.errors && lastResult.errors.length > 0 && (
                    <div className="text-atlas-warn mt-2">
                      Some courses didn't fully sync: {lastResult.errors.join("; ")}
                    </div>
                  )}
                </>
              ) : (
                <div className="text-atlas-bad">{lastResult.detail ?? "Sync did not complete."}</div>
              )}
            </div>
          )}

          <div className="card mt-4 opacity-60">
            <div className="font-medium">Schoology, Blackboard</div>
            <div className="text-sm text-atlas-muted mt-1">Coming soon.</div>
          </div>
        </Section>
      )}

      <Modal
        open={connectOpen}
        onClose={closeConnectModal}
        title="Connect PowerSchool"
        footer={
          <>
            <button className="btn-ghost" onClick={closeConnectModal}>Cancel</button>
            <button className="btn-primary" form="ps-connect-form" disabled={connecting}>
              {connecting ? "Connecting…" : "Connect"}
            </button>
          </>
        }
      >
        <form id="ps-connect-form" onSubmit={connect} className="space-y-3">
          <div className="flex gap-1 p-1 rounded-lg bg-atlas-panel2 text-sm">
            <button
              type="button"
              className={`flex-1 py-1.5 rounded-md ${mode === "password" ? "bg-atlas-panel shadow-soft" : "text-atlas-muted"}`}
              onClick={() => setMode("password")}
            >
              Username &amp; password
            </button>
            <button
              type="button"
              className={`flex-1 py-1.5 rounded-md ${mode === "cookie" ? "bg-atlas-panel shadow-soft" : "text-atlas-muted"}`}
              onClick={() => setMode("cookie")}
            >
              Session cookie
            </button>
          </div>

          {error && <div className="text-sm text-atlas-bad">{error}</div>}
          <div>
            <label className="label">Portal URL</label>
            <div className="flex gap-2">
              <input
                className="input"
                required
                placeholder="https://yourdistrict.powerschool.com"
                value={form.base_url}
                onChange={(e) => setForm({ ...form, base_url: e.target.value })}
              />
              <button
                type="button"
                className="btn-ghost shrink-0"
                disabled={probing || !form.base_url.trim()}
                onClick={testUrl}
              >
                {probing ? "Testing…" : "Test URL"}
              </button>
            </div>
          </div>

          {probeError && <div className="text-sm text-atlas-bad">{probeError}</div>}
          {probeResult && (
            <div className="card text-xs space-y-1.5 bg-atlas-panel2">
              <div>
                <span className="text-atlas-muted">Fetched: </span>
                {probeResult.final_url} ({probeResult.status_code})
              </div>
              {probeResult.page_title && (
                <div>
                  <span className="text-atlas-muted">Page title: </span>
                  {probeResult.page_title}
                </div>
              )}
              <div>
                {probeResult.login_type === "legacy" ? (
                  <span className="text-atlas-good">✓ Found a login form Atlas can automate</span>
                ) : probeResult.login_type === "cas" ? (
                  <span className="text-atlas-warn">
                    ⚠ Found a login form, but this district uses a newer ticket-based (CAS) login
                    flow.{" "}
                    {probeResult.browser_fallback_available
                      ? "Username & password mode will automatically fall back to real-browser " +
                        "automation for this — it may take longer to sync and isn't guaranteed to " +
                        "work if the login page also has anti-bot protection. If it doesn't, " +
                        "Session cookie mode is the reliable fallback."
                      : "Username & password mode can't handle this here — Atlas's hosted " +
                        "environment can't run the real-browser automation this flow needs. " +
                        "Use Session cookie mode instead."}{" "}
                    {mode !== "cookie" && (
                      <button type="button" className="underline" onClick={() => setMode("cookie")}>
                        Switch to Session cookie mode
                      </button>
                    )}
                  </span>
                ) : (
                  <span className="text-atlas-bad">
                    ✗ No login form found — check the portal URL, or this district may require SSO.
                    {" "}
                    {mode !== "cookie" && (
                      <button type="button" className="underline" onClick={() => setMode("cookie")}>
                        Switch to Session cookie mode
                      </button>
                    )}
                  </span>
                )}
              </div>
              {probeResult.forms.length > 0 && (
                <div>
                  <span className="text-atlas-muted">Forms found: </span>
                  {probeResult.forms.map((f, i) => f.id || `#${i}`).join(", ")}
                </div>
              )}
              <button
                type="button"
                className="text-atlas-accent underline"
                onClick={() => setShowSnippet((v) => !v)}
              >
                {showSnippet ? "Hide" : "Show"} raw HTML snippet
              </button>
              {showSnippet && (
                <pre className="whitespace-pre-wrap break-all bg-atlas-bg p-2 rounded max-h-64 overflow-auto">
                  {probeResult.html_snippet}
                </pre>
              )}
            </div>
          )}

          {mode === "password" ? (
            <>
              <p className="text-sm text-atlas-muted">
                Atlas logs in the same way you do on the PowerSchool website. Your password is
                stored encrypted and only used to pull your grades.
              </p>
              <div>
                <label className="label">Username</label>
                <input
                  className="input"
                  required
                  value={form.username}
                  onChange={(e) => setForm({ ...form, username: e.target.value })}
                />
              </div>
              <div>
                <label className="label">Password</label>
                <input
                  className="input"
                  type="password"
                  required
                  value={form.password}
                  onChange={(e) => setForm({ ...form, password: e.target.value })}
                />
              </div>
            </>
          ) : (
            <>
              <div className="text-sm text-atlas-muted space-y-1">
                <p>
                  Some districts use SSO (Google/Microsoft/Clever) or a newer login flow Atlas
                  can't automate directly. Instead, reuse a session from your own browser:
                </p>
                <ol className="list-decimal list-inside space-y-0.5">
                  <li>Log into PowerSchool in this browser, as you normally do.</li>
                  <li>Open Developer Tools (F12) → the <b>Network</b> tab → reload the page.</li>
                  <li>Click any request to your PowerSchool domain (e.g. "home.html").</li>
                  <li>In Request Headers, find <b>Cookie</b> and copy its entire value.</li>
                  <li>Paste it below.</li>
                </ol>
                <p>
                  This session will expire (typically within a day) — when a sync starts failing,
                  just repeat these steps and reconnect with a fresh cookie.
                </p>
              </div>
              <div>
                <label className="label">Session cookie</label>
                <textarea
                  className="input font-mono text-xs"
                  rows={4}
                  required
                  placeholder="JSESSIONID=...; other_cookie=...;"
                  value={form.cookie}
                  onChange={(e) => setForm({ ...form, cookie: e.target.value })}
                />
              </div>
            </>
          )}
        </form>
      </Modal>
    </AppShell>
  );
}
