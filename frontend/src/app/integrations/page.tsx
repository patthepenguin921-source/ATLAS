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
  config?: { auth_mode?: "password" | "cookie"; domain?: string; username?: string };
}

interface SyncResult {
  status: string;
  courses?: number;
  assignments?: number;
  grades?: number;
  events?: number;
  documents?: number;
  links?: number;
  announcements?: number;
  errors?: string[];
  detail?: string;
}

interface SchoologyVerifyResult {
  api_uid: string;
  section_count: number;
}

interface ProbeResult {
  requested_url: string;
  final_url: string;
  status_code: number;
  page_title: string | null;
  has_login_form: boolean;
  login_type: "legacy" | "pcas" | "cas" | null;
  browser_fallback_available: boolean;
  forms: { id: string | null; action: string | null; input_names: string[] }[];
  html_snippet: string;
}

interface DebugScrapeResult {
  final_url: string;
  status_code: number;
  ccid_row_count: number;
  header_row_html: string | null;
  sample_row_html: string[];
}

interface SchoologyMaterialItem {
  name: string;
  type: string | null;
  folder: string | null;
  href: string;
}

interface SchoologyWalkStep {
  requested_url: string;
  final_url?: string;
  status_code?: number;
  depth?: number;
  title?: string | null;
  raw_link_count?: number;
  parsed_count?: number;
  looks_like_login?: boolean;
  likely_js_shell?: boolean;
  script_count?: number;
  body_text_len?: number;
  folders?: string[];
  items?: string[];
  sample_links?: { text?: string; href?: string }[];
}

interface SchoologyProbedSection {
  section: { id: string; name: string };
  materials_url?: string | null;
  items: SchoologyMaterialItem[];
  walk_trace?: SchoologyWalkStep[];
  error?: string | null;
}

interface SchoologyDebugResult {
  probed?: SchoologyProbedSection[];
  available_sections?: string[];
  note?: string;
}

// Turn a raw materials walk trace into one plain-language sentence explaining
// why a course came back with no items — the difference between "the session
// bounced to a login page", "the page loaded but had nothing on it", and "the
// page couldn't be reached" is invisible from an empty list alone.
function diagnoseWalk(trace: SchoologyWalkStep[]): string {
  if (trace.some((s) => s.looks_like_login)) {
    return "The Schoology session was bounced to a login page while loading this course — the login may not have full access to it (e.g. a parent account whose materials live elsewhere), or the session expired.";
  }
  if (trace.some((s) => s.status_code && s.status_code >= 400)) {
    const bad = trace.find((s) => s.status_code && s.status_code >= 400)!;
    return `This course's materials page couldn't be reached (HTTP ${bad.status_code}). The course id may not be visible to this login.`;
  }
  if (trace.some((s) => s.likely_js_shell)) {
    return "The page came back as a JavaScript shell — its real content is rendered in the browser and isn't in the HTML the server can fetch. This needs a different fetch approach (a data endpoint), not a parser tweak.";
  }
  if (trace.every((s) => (s.raw_link_count ?? 0) === 0)) {
    return "The materials page loaded but contained no links at all — its contents are likely loaded dynamically (JavaScript), which the login scraper can't see yet.";
  }
  return "The materials page loaded but nothing on it was recognized as a folder, file, or link — the raw links below show what the page actually contained.";
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
  const [debugging, setDebugging] = useState(false);
  const [debugResult, setDebugResult] = useState<DebugScrapeResult | null>(null);
  const [debugError, setDebugError] = useState<string | null>(null);
  const [schoologyDebugging, setSchoologyDebugging] = useState(false);
  const [schoologyDebugResult, setSchoologyDebugResult] = useState<SchoologyDebugResult | null>(null);
  const [schoologyDebugError, setSchoologyDebugError] = useState<string | null>(null);
  const [schoologyDebugQuery, setSchoologyDebugQuery] = useState("");

  // Schoology connect state — a single login (username + password, the same
  // credentials used in a browser) is the only required credential. A
  // personal API key is an optional "Advanced" extra that additionally
  // unlocks assignments/events sync (there's no scraper for those yet, only
  // for materials).
  const [schoologyOpen, setSchoologyOpen] = useState(false);
  const [schoologyConnecting, setSchoologyConnecting] = useState(false);
  const [schoologyError, setSchoologyError] = useState<string | null>(null);
  const [schoologyForm, setSchoologyForm] = useState({
    domain: "",
    username: "",
    password: "",
    consumer_key: "",
    consumer_secret: "",
  });
  const [showApiKeyAdvanced, setShowApiKeyAdvanced] = useState(false);
  const [verifying, setVerifying] = useState(false);
  const [verifyResult, setVerifyResult] = useState<SchoologyVerifyResult | null>(null);
  const [verifyError, setVerifyError] = useState<string | null>(null);

  async function load() {
    setIntegrations(await apiGet<Integration[]>("/integrations"));
  }
  useEffect(() => {
    load();
  }, []);

  const powerschool = integrations?.find((i) => i.provider === "powerschool");
  const schoology = integrations?.find((i) => i.provider === "schoology");

  function closeSchoologyModal() {
    setSchoologyOpen(false);
    setVerifyResult(null);
    setVerifyError(null);
    setSchoologyError(null);
  }

  function openSchoologyModal() {
    // Domain and username aren't secret (an email is fine to show back) and
    // the backend now returns them in `config`, so editing starts from what's
    // actually saved instead of a blank slate — only password/API-key secret
    // stay blank (never sent back from the backend, only encrypted at rest).
    setSchoologyForm({
      domain: schoology?.config?.domain ?? "",
      username: schoology?.config?.username ?? "",
      password: "",
      consumer_key: "",
      consumer_secret: "",
    });
    setShowApiKeyAdvanced(false);
    setVerifyResult(null);
    setVerifyError(null);
    setSchoologyError(null);
    setSchoologyOpen(true);
  }

  async function verifySchoology() {
    if (!schoologyForm.consumer_key.trim() || !schoologyForm.consumer_secret.trim()) return;
    setVerifying(true);
    setVerifyError(null);
    setVerifyResult(null);
    try {
      const result = await apiPost<SchoologyVerifyResult>("/integrations/schoology/verify", {
        domain: schoologyForm.domain,
        username: schoologyForm.username,
        password: schoologyForm.password,
        consumer_key: schoologyForm.consumer_key,
        consumer_secret: schoologyForm.consumer_secret,
      });
      setVerifyResult(result);
    } catch (err: any) {
      setVerifyError(err.message ?? "Those credentials didn't work.");
    } finally {
      setVerifying(false);
    }
  }

  async function connectSchoology(e: React.FormEvent) {
    e.preventDefault();
    setSchoologyConnecting(true);
    setSchoologyError(null);
    setLastResult(null);
    try {
      const result = await apiPost<SyncResult>("/integrations/schoology/connect", {
        domain: schoologyForm.domain,
        username: schoologyForm.username,
        password: schoologyForm.password,
        ...(schoologyForm.consumer_key.trim() && schoologyForm.consumer_secret.trim()
          ? { consumer_key: schoologyForm.consumer_key, consumer_secret: schoologyForm.consumer_secret }
          : {}),
      });
      setLastResult(result);
      closeSchoologyModal();
      await load();
    } catch (err: any) {
      setSchoologyError(err.message ?? "Connection failed.");
    } finally {
      setSchoologyConnecting(false);
    }
  }

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

  async function debugScrape() {
    setDebugging(true);
    setDebugError(null);
    setDebugResult(null);
    try {
      const result = await apiGet<DebugScrapeResult>("/integrations/powerschool/debug-scrape");
      setDebugResult(result);
    } catch (err: any) {
      setDebugError(err.message ?? "Could not fetch the grades page.");
    } finally {
      setDebugging(false);
    }
  }

  async function debugFetchSchoology() {
    setSchoologyDebugging(true);
    setSchoologyDebugError(null);
    setSchoologyDebugResult(null);
    try {
      const q = schoologyDebugQuery.trim();
      const path = q
        ? `/integrations/schoology/debug-walk-materials?q=${encodeURIComponent(q)}`
        : "/integrations/schoology/debug-walk-materials";
      const result = await apiGet<SchoologyDebugResult>(path);
      setSchoologyDebugResult(result);
    } catch (err: any) {
      setSchoologyDebugError(err.message ?? "Could not fetch from Schoology.");
    } finally {
      setSchoologyDebugging(false);
    }
  }

  function openConnectModal() {
    // Always start blank — credentials aren't sent back from the backend
    // (only encrypted at rest), so there's nothing safe to pre-fill, and
    // reusing stale values here would be exactly the "saving the wrong
    // login" confusion this is meant to avoid. Re-opening this same modal
    // while already connected re-submits to the same connect endpoints,
    // which overwrite the saved login rather than creating a duplicate.
    setForm({ base_url: "", username: "", password: "", cookie: "" });
    setError(null);
    setMode(powerschool?.config?.auth_mode === "cookie" ? "cookie" : "password");
    setConnectOpen(true);
  }

  async function disconnect(provider: string) {
    const label = provider === "schoology" ? "Schoology" : "PowerSchool";
    if (!confirm(`Disconnect ${label}? This removes your saved credentials; imported data stays.`)) return;
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
                    <button className="btn-ghost" disabled={debugging} onClick={debugScrape}>
                      {debugging ? "Fetching…" : "Debug scrape"}
                    </button>
                    <button className="btn-ghost" onClick={openConnectModal}>
                      Edit login
                    </button>
                    <button className="btn-ghost" onClick={() => disconnect("powerschool")}>
                      Disconnect
                    </button>
                  </>
                ) : (
                  <button className="btn-primary" onClick={openConnectModal}>
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
                    {lastResult.courses ?? 0} courses · {lastResult.assignments ?? 0} assignments
                    {lastResult.grades !== undefined && ` · ${lastResult.grades} grades`}
                    {lastResult.events !== undefined && ` · ${lastResult.events} calendar items`}
                    {lastResult.documents !== undefined && ` · ${lastResult.documents} files`}
                    {lastResult.links !== undefined && ` · ${lastResult.links} links`}
                    {" imported"}
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

          {debugError && (
            <div className="card mt-4 text-sm text-atlas-bad">{debugError}</div>
          )}
          {debugResult && (
            <div className="card mt-4 text-xs space-y-1.5">
              <div className="font-medium text-sm mb-1">Raw grades-page scrape</div>
              <div>
                <span className="text-atlas-muted">Fetched: </span>
                {debugResult.final_url} ({debugResult.status_code})
              </div>
              <div>
                <span className="text-atlas-muted">Course rows found: </span>
                {debugResult.ccid_row_count}
              </div>
              {debugResult.header_row_html && (
                <div>
                  <div className="text-atlas-muted">Header row:</div>
                  <pre className="whitespace-pre-wrap break-all bg-atlas-panel2 p-2 rounded max-h-40 overflow-auto">
                    {debugResult.header_row_html}
                  </pre>
                </div>
              )}
              {debugResult.sample_row_html.map((html, i) => (
                <div key={i}>
                  <div className="text-atlas-muted">Sample course row {i + 1}:</div>
                  <pre className="whitespace-pre-wrap break-all bg-atlas-panel2 p-2 rounded max-h-40 overflow-auto">
                    {html}
                  </pre>
                </div>
              ))}
            </div>
          )}

          <div className="card mt-4">
            <div className="flex items-center justify-between">
              <div>
                <div className="font-medium flex items-center gap-2">
                  Schoology
                  {schoology && <Badge tone={STATUS_TONE[schoology.status]}>{schoology.status}</Badge>}
                </div>
                <div className="text-sm text-atlas-muted mt-1">
                  {schoology
                    ? schoology.last_synced_at
                      ? `Last synced ${new Date(schoology.last_synced_at).toLocaleString()}`
                      : "Connected — not yet synced"
                    : "Auto-import your courses and every course folder's files, slideshows, and links using your Schoology login. Add an optional API key to also sync assignments and your week-at-a-glance. Grades stay in PowerSchool."}
                </div>
                {schoology?.last_error && (
                  <div className="text-sm text-atlas-bad mt-1">{schoology.last_error}</div>
                )}
              </div>
              <div className="flex items-center gap-2 shrink-0">
                {schoology ? (
                  <>
                    <button
                      className="btn-ghost"
                      disabled={syncingProvider === "schoology"}
                      onClick={() => sync("schoology")}
                    >
                      {syncingProvider === "schoology" ? "Syncing…" : "Sync now"}
                    </button>
                    <input
                      className="input w-32 text-xs"
                      placeholder="AP Physics…"
                      value={schoologyDebugQuery}
                      onChange={(e) => setSchoologyDebugQuery(e.target.value)}
                      onKeyDown={(e) => e.key === "Enter" && debugFetchSchoology()}
                    />
                    <button className="btn-ghost" disabled={schoologyDebugging} onClick={debugFetchSchoology}>
                      {schoologyDebugging ? "Fetching…" : "Debug materials"}
                    </button>
                    <button className="btn-ghost" onClick={openSchoologyModal}>
                      Edit login
                    </button>
                    <button className="btn-ghost" onClick={() => disconnect("schoology")}>
                      Disconnect
                    </button>
                  </>
                ) : (
                  <button className="btn-primary" onClick={openSchoologyModal}>
                    Connect
                  </button>
                )}
              </div>
            </div>
          </div>

          {schoologyDebugError && (
            <div className="card mt-4 text-sm text-atlas-bad">{schoologyDebugError}</div>
          )}
          {schoologyDebugResult && (
            <div className="card mt-4 text-xs space-y-3">
              <div className="font-medium text-sm">Materials found via login</div>
              {schoologyDebugResult.note ? (
                <div className="text-atlas-muted">
                  {schoologyDebugResult.note}
                  {schoologyDebugResult.available_sections && schoologyDebugResult.available_sections.length > 0 && (
                    <>
                      {" "}Try one of: {schoologyDebugResult.available_sections.join(", ")}
                    </>
                  )}
                </div>
              ) : (
                <>
                  <div className="text-atlas-muted">
                    Walks each course's materials via your Schoology login (not the API key) and
                    lists every real folder/file/link found, with Schoology's page navigation
                    filtered out.
                  </div>
                  {schoologyDebugResult.probed?.map((p) => (
                    <div key={p.section.id} className="space-y-1.5 border-t border-atlas-border pt-3 first:border-0 first:pt-0">
                      <div className="font-medium">{p.section.name}</div>
                      {p.materials_url && (
                        <div className="text-atlas-muted break-all">
                          Walked exactly:{" "}
                          <a href={p.materials_url} target="_blank" rel="noreferrer" className="underline">
                            {p.materials_url}
                          </a>
                        </div>
                      )}
                      {p.error && (
                        <div className="text-atlas-bad">Couldn&apos;t reach this course: {p.error}</div>
                      )}
                      {p.items.length === 0 ? (
                        <>
                          <div className="text-atlas-muted">No items found.</div>
                          {p.walk_trace && p.walk_trace.length > 0 && (
                            <div className="mt-1.5 space-y-1">
                              <div className="text-atlas-muted">
                                {diagnoseWalk(p.walk_trace)}
                              </div>
                              <details>
                                <summary className="cursor-pointer text-atlas-muted">
                                  Pages checked ({p.walk_trace.length})
                                </summary>
                                <ul className="mt-1 space-y-2 font-mono text-[11px] leading-tight">
                                  {p.walk_trace.map((step, i) => (
                                    <li key={i} className="break-all text-atlas-muted">
                                      <div>
                                        [{step.status_code ?? "?"}]
                                        {step.looks_like_login ? " ⚠ login-wall" : ""}
                                        {step.likely_js_shell ? " ⚠ js-shell" : ""}{" "}
                                        {step.parsed_count ?? 0} of {step.raw_link_count ?? 0} links ·{" "}
                                        {step.final_url ?? step.requested_url}
                                      </div>
                                      {step.sample_links && step.sample_links.length > 0 && (
                                        <ul className="mt-0.5 ml-3 space-y-0.5 opacity-80">
                                          {step.sample_links.map((l, j) => (
                                            <li key={j}>
                                              {l.text ? `"${l.text}" ` : ""}→ {l.href}
                                            </li>
                                          ))}
                                        </ul>
                                      )}
                                    </li>
                                  ))}
                                </ul>
                              </details>
                            </div>
                          )}
                        </>
                      ) : (
                        <ul className="space-y-1">
                          {p.items.map((item, i) => (
                            <li key={i} className="flex flex-wrap items-baseline gap-x-2">
                              <span>{item.folder ? `${item.folder} · ` : ""}{item.name}</span>
                              {item.type && <span className="text-atlas-muted">({item.type})</span>}
                              {item.href && (
                                <a
                                  href={item.href}
                                  target="_blank"
                                  rel="noreferrer"
                                  className="text-atlas-muted break-all underline"
                                >
                                  {item.href}
                                </a>
                              )}
                            </li>
                          ))}
                        </ul>
                      )}
                    </div>
                  ))}
                </>
              )}
            </div>
          )}

          <div className="card mt-4 opacity-60">
            <div className="font-medium">Blackboard</div>
            <div className="text-sm text-atlas-muted mt-1">Coming soon.</div>
          </div>
        </Section>
      )}

      <Modal
        open={connectOpen}
        onClose={closeConnectModal}
        title={powerschool ? "Update PowerSchool login" : "Connect PowerSchool"}
        footer={
          <>
            <button className="btn-ghost" onClick={closeConnectModal}>Cancel</button>
            <button className="btn-primary" form="ps-connect-form" disabled={connecting}>
              {connecting ? "Saving…" : powerschool ? "Save" : "Connect"}
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
                {probeResult.login_type === "legacy" || probeResult.login_type === "pcas" ? (
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

      <Modal
        open={schoologyOpen}
        onClose={closeSchoologyModal}
        title={schoology ? "Update Schoology login" : "Connect Schoology"}
        footer={
          <>
            <button className="btn-ghost" onClick={closeSchoologyModal}>Cancel</button>
            <button className="btn-primary" form="schoology-connect-form" disabled={schoologyConnecting}>
              {schoologyConnecting ? "Saving…" : schoology ? "Save" : "Connect"}
            </button>
          </>
        }
      >
        <form id="schoology-connect-form" onSubmit={connectSchoology} className="space-y-3">
          <p className="text-sm text-atlas-muted">
            Atlas logs in the same way you do on the Schoology website, to read your courses and
            course materials (files, slideshows, links). Your password is stored encrypted. Grades
            are never touched — those stay in PowerSchool.
          </p>

          {schoologyError && <div className="text-sm text-atlas-bad">{schoologyError}</div>}

          <div>
            <label className="label">Schoology web address</label>
            <input
              className="input"
              required
              placeholder="https://lexington1.schoology.com"
              value={schoologyForm.domain}
              onChange={(e) => setSchoologyForm({ ...schoologyForm, domain: e.target.value })}
            />
          </div>
          <div>
            <label className="label">Username / email</label>
            <input
              className="input"
              required
              value={schoologyForm.username}
              onChange={(e) => setSchoologyForm({ ...schoologyForm, username: e.target.value })}
            />
          </div>
          <div>
            <label className="label">Password</label>
            <input
              className="input"
              type="password"
              required
              value={schoologyForm.password}
              onChange={(e) => setSchoologyForm({ ...schoologyForm, password: e.target.value })}
            />
          </div>

          <button
            type="button"
            className="text-sm text-atlas-accent underline"
            onClick={() => setShowApiKeyAdvanced((v) => !v)}
          >
            {showApiKeyAdvanced ? "Hide" : "Show"} advanced: optional API key
          </button>

          {showApiKeyAdvanced && (
            <div className="space-y-3 rounded-lg bg-atlas-panel2 p-3">
              <p className="text-sm text-atlas-muted">
                Optional. A personal API key additionally unlocks assignments and week-at-a-glance
                sync (the login above can't read those yet — only courses and materials). Generate
                one at{" "}
                <b>
                  {schoologyForm.domain
                    ? `${schoologyForm.domain.replace(/\/$/, "")}/api`
                    : "your-schoology-site/api"}
                </b>{" "}
                → <b>Request API credentials</b>.
              </p>
              <div>
                <label className="label">Consumer key</label>
                <input
                  className="input font-mono text-xs"
                  value={schoologyForm.consumer_key}
                  onChange={(e) => setSchoologyForm({ ...schoologyForm, consumer_key: e.target.value })}
                />
              </div>
              <div>
                <label className="label">Consumer secret</label>
                <input
                  className="input font-mono text-xs"
                  type="password"
                  value={schoologyForm.consumer_secret}
                  onChange={(e) => setSchoologyForm({ ...schoologyForm, consumer_secret: e.target.value })}
                />
              </div>
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  className="btn-ghost"
                  disabled={verifying || !schoologyForm.consumer_key.trim() || !schoologyForm.consumer_secret.trim()}
                  onClick={verifySchoology}
                >
                  {verifying ? "Checking…" : "Test key"}
                </button>
                {verifyResult && (
                  <span className="text-sm text-atlas-good">
                    ✓ Works — found {verifyResult.section_count} courses
                  </span>
                )}
                {verifyError && <span className="text-sm text-atlas-bad">{verifyError}</span>}
              </div>
            </div>
          )}
        </form>
      </Modal>
    </AppShell>
  );
}
