"use client";

import { Suspense, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { AppShell } from "@/components/AppShell";
import { Empty, Loading, Badge, Modal, Section, Ring, ActionMenu } from "@/components/ui";
import { apiGet, apiPost, apiPatch, apiDelete } from "@/lib/api";

interface Profile {
  id: string;
  full_name?: string | null;
  school?: string | null;
  grade_level?: string | null;
  gpa_goal?: number | null;
  timezone?: string | null;
}

interface Fact {
  id: string;
  key: string;
  value: string;
  category?: string | null;
  updated_at: string;
}

const TABS = ["Account", "Integrations", "Memory"] as const;
type Tab = (typeof TABS)[number];

export default function SettingsPage() {
  return (
    <Suspense fallback={<Loading />}>
      <SettingsPageInner />
    </Suspense>
  );
}

function SettingsPageInner() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const requestedTab = searchParams.get("tab");
  const initialTab: Tab = TABS.includes(requestedTab as Tab) ? (requestedTab as Tab) : "Account";
  const [tab, setTab] = useState<Tab>(initialTab);

  function selectTab(t: Tab) {
    setTab(t);
    router.replace(t === "Account" ? "/settings" : `/settings?tab=${t}`);
  }

  return (
    <AppShell title="Settings" subtitle="Your account, integrations, and what Atlas remembers about you">
      <div className="flex gap-1 mb-6 border-b border-atlas-border">
        {TABS.map((t) => (
          <button
            key={t}
            onClick={() => selectTab(t)}
            className={`px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors ${
              tab === t
                ? "border-atlas-accent text-atlas-text"
                : "border-transparent text-atlas-muted hover:text-atlas-text"
            }`}
          >
            {t}
          </button>
        ))}
      </div>
      {tab === "Account" ? <AccountTab /> : tab === "Integrations" ? <IntegrationsTab /> : <MemoryTab />}
    </AppShell>
  );
}

function AccountTab() {
  const [profile, setProfile] = useState<Profile | null>(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    apiGet<Profile>("/profile").then(setProfile);
  }, []);

  async function save() {
    if (!profile) return;
    setSaving(true);
    setSaved(false);
    try {
      const updated = await apiPatch<Profile>("/profile", {
        full_name: profile.full_name,
        school: profile.school,
        grade_level: profile.grade_level,
        gpa_goal: profile.gpa_goal,
        timezone: profile.timezone,
      });
      setProfile(updated);
      setSaved(true);
    } finally {
      setSaving(false);
    }
  }

  if (!profile) return <Loading />;

  return (
    <Section title="Profile">
      <div className="card grid sm:grid-cols-2 gap-4 max-w-2xl">
        <div>
          <label className="label">Full name</label>
          <input
            className="input"
            value={profile.full_name ?? ""}
            onChange={(e) => setProfile({ ...profile, full_name: e.target.value })}
          />
        </div>
        <div>
          <label className="label">School</label>
          <input
            className="input"
            value={profile.school ?? ""}
            onChange={(e) => setProfile({ ...profile, school: e.target.value })}
          />
        </div>
        <div>
          <label className="label">Grade level</label>
          <input
            className="input"
            value={profile.grade_level ?? ""}
            onChange={(e) => setProfile({ ...profile, grade_level: e.target.value })}
          />
        </div>
        <div>
          <label className="label">GPA goal</label>
          <input
            className="input"
            type="number"
            step="0.01"
            value={profile.gpa_goal ?? ""}
            onChange={(e) =>
              setProfile({ ...profile, gpa_goal: e.target.value ? Number(e.target.value) : null })
            }
          />
        </div>
        <div>
          <label className="label">Timezone</label>
          <input
            className="input"
            placeholder="America/New_York"
            value={profile.timezone ?? ""}
            onChange={(e) => setProfile({ ...profile, timezone: e.target.value })}
          />
        </div>
        <div className="sm:col-span-2 flex items-center gap-3 pt-1">
          <button className="btn-primary" disabled={saving} onClick={save}>
            {saving ? "Saving…" : "Save changes"}
          </button>
          {saved && <span className="text-xs text-atlas-good">Saved</span>}
        </div>
      </div>
    </Section>
  );
}

function MemoryTab() {
  const [facts, setFacts] = useState<Fact[] | null>(null);
  const [removingId, setRemovingId] = useState<string | null>(null);

  async function load() {
    setFacts(await apiGet<Fact[]>("/agents/facts"));
  }
  useEffect(() => {
    load();
  }, []);

  async function forget(id: string) {
    setRemovingId(id);
    try {
      await apiDelete(`/agents/facts/${id}`);
      setFacts((f) => f?.filter((x) => x.id !== id) ?? f);
    } finally {
      setRemovingId(null);
    }
  }

  return (
    <Section title="What Atlas has learned about you">
      {!facts && <Loading />}
      {facts && !facts.length && (
        <Empty>
          Nothing yet. As you chat with Atlas, durable preferences and context it picks up
          (like study habits or goals) will show up here — never homework specifics or grades,
          just things worth remembering long-term.
        </Empty>
      )}
      <div className="space-y-2">
        {facts?.map((f) => (
          <div key={f.id} className="card flex items-center justify-between gap-4">
            <div>
              <div className="text-sm">{f.value}</div>
              <div className="text-xs text-atlas-muted mt-1">
                {f.category ?? "context"} · updated {new Date(f.updated_at).toLocaleDateString()}
              </div>
            </div>
            <button
              className="btn-ghost text-xs py-1 shrink-0"
              disabled={removingId === f.id}
              onClick={() => forget(f.id)}
            >
              {removingId === f.id ? "Removing…" : "Forget"}
            </button>
          </div>
        ))}
      </div>
    </Section>
  );
}

/* ---------------------------------------------------------------------- *
 * Integrations tab — PowerSchool & Schoology, health-first layout.
 *
 * "Health" leads instead of raw timestamps: each connected provider gets a
 * ring showing how close it is to its next expected sync (twice daily,
 * ~7am/4pm America/New_York — see automation/README.md), so a silently
 * broken scheduler shows up as a red ring the moment this page loads
 * instead of requiring someone to read a timestamp and do the math.
 * ---------------------------------------------------------------------- */

interface Integration {
  provider: string;
  display_name?: string | null;
  status: "idle" | "running" | "success" | "error";
  last_synced_at?: string | null;
  last_error?: string | null;
  enabled: boolean;
  config?: {
    auth_mode?: "password" | "cookie"; domain?: string; username?: string;
    google_connected?: boolean;
  };
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
  downloaded?: boolean;
  download_content_type?: string;
  download_size_bytes?: number;
  download_error?: string;
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
  error?: string;
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
  const errorStep = trace.find((s) => s.error);
  if (errorStep) {
    return `Couldn't log in to load this page: ${errorStep.error}`;
  }
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

// Providers sync twice daily, ~7am/4pm America/New_York (see
// automation/README.md) — a ~9h and a ~15h gap alternating, averaging 12h.
// Ring fill counts down against that 12h nominal cadence; OVERDUE_HOURS adds
// buffer past the longer of the two real gaps before calling it broken,
// rather than flagging every afternoon sync as "late" against a flat 12h.
const SYNC_CADENCE_HOURS = 12;
const OVERDUE_HOURS = 20;

interface SyncHealth {
  tone: "good" | "warn" | "bad" | "default";
  percent: number;
  label: string;
}

function syncHealth(integration: Integration | undefined): SyncHealth {
  if (!integration) return { tone: "default", percent: 0, label: "Not connected" };
  if (integration.status === "running") return { tone: "warn", percent: 50, label: "Syncing now…" };
  if (!integration.enabled) return { tone: "default", percent: 100, label: "Sync paused" };
  if (!integration.last_synced_at) return { tone: "warn", percent: 15, label: "Connected — not yet synced" };

  const elapsedHours = (Date.now() - new Date(integration.last_synced_at).getTime()) / 3_600_000;
  if (elapsedHours >= OVERDUE_HOURS) {
    const days = Math.floor(elapsedHours / 24);
    return {
      tone: "bad",
      percent: 0,
      label:
        days >= 1
          ? `${days} day${days === 1 ? "" : "s"} overdue — sync isn't running`
          : `${Math.round(elapsedHours)}h overdue — sync isn't running`,
    };
  }
  const percent = Math.max(0, Math.min(100, Math.round(100 - (elapsedHours / SYNC_CADENCE_HOURS) * 100)));
  if (percent < 40) {
    const hoursLeft = Math.max(0, Math.round(SYNC_CADENCE_HOURS - elapsedHours));
    return { tone: "warn", percent, label: hoursLeft <= 0 ? "Due any time now" : `Due in ~${hoursLeft}h` };
  }
  return { tone: "good", percent, label: "Healthy" };
}

const STATUS_TONE: Record<string, "good" | "warn" | "bad" | "default"> = {
  success: "good",
  running: "warn",
  error: "bad",
  idle: "default",
};

function IntegrationsTab() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [integrations, setIntegrations] = useState<Integration[] | null>(null);
  const [googleConnecting, setGoogleConnecting] = useState(false);
  const [googleDisconnecting, setGoogleDisconnecting] = useState(false);
  const [googleStatus, setGoogleStatus] = useState<{ ok: boolean; text: string } | null>(null);
  const [connectOpen, setConnectOpen] = useState(false);
  const [connecting, setConnecting] = useState(false);
  const [syncingProvider, setSyncingProvider] = useState<string | null>(null);
  const [cancelingProvider, setCancelingProvider] = useState<string | null>(null);
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

  // Google's OAuth consent screen redirects the browser back here (a
  // full-page navigation from the backend's /integrations/google/callback,
  // not a fetch response) with ?google=connected|error&detail=... — surface
  // it once, then strip the params so refreshing the page doesn't re-show it.
  useEffect(() => {
    const google = searchParams.get("google");
    if (!google) return;
    setGoogleStatus(
      google === "connected"
        ? { ok: true, text: "Google Drive connected." }
        : { ok: false, text: searchParams.get("detail") || "Couldn't connect Google Drive." }
    );
    router.replace("/settings?tab=Integrations");
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchParams]);

  async function connectGoogle() {
    setGoogleConnecting(true);
    setGoogleStatus(null);
    try {
      const { url } = await apiGet<{ url: string }>("/integrations/google/connect");
      window.location.href = url;
    } catch (err: any) {
      setGoogleStatus({ ok: false, text: err.message ?? "Couldn't start Google connect." });
      setGoogleConnecting(false);
    }
  }

  async function disconnectGoogle() {
    setGoogleDisconnecting(true);
    try {
      await apiPost("/integrations/google/disconnect");
      await load();
    } finally {
      setGoogleDisconnecting(false);
    }
  }

  const powerschool = integrations?.find((i) => i.provider === "powerschool");
  const schoology = integrations?.find((i) => i.provider === "schoology");
  const powerschoolHealth = syncHealth(powerschool);
  const schoologyHealth = syncHealth(schoology);
  const overdue = [
    powerschool && powerschoolHealth.tone === "bad" ? "PowerSchool" : null,
    schoology && schoologyHealth.tone === "bad" ? "Schoology" : null,
  ].filter(Boolean) as string[];

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
      // A real account's sync can take several chunks to finish (see
      // run_sync_step's docstring on the backend) — each call only ever
      // runs one bounded chunk and comes back in well under a couple
      // minutes, so this loops, calling again whenever the response says
      // there's more to do, instead of holding one request open for the
      // sync's entire duration (the fragile pattern — a dropped WiFi, a
      // laptop going to sleep, a proxy's idle timeout — that made "Sync
      // now" unreliable in the first place). The per-call timeout here is
      // a backstop for a connection that stalls without ever coming back;
      // it's comfortably above the backend's own per-chunk timeout so that
      // one wins first in the normal case.
      let result: SyncResult;
      let chunks = 0;
      const MAX_CHUNKS = 60; // generous ceiling against a runaway loop, not a real-world limit
      do {
        result = await apiPost<SyncResult>(`/integrations/${provider}/sync`, undefined, 170_000);
        setLastResult(result);
        chunks += 1;
      } while (result.status === "running" && chunks < MAX_CHUNKS);
      await load();
    } catch (err: any) {
      setError(err.message ?? "Sync failed.");
    } finally {
      setSyncingProvider(null);
    }
  }

  // Clears a sync stuck on "running" (e.g. one that outlived the backend's
  // own timeout) so the account isn't blocked waiting for the next scheduled
  // sweep. This can't reach into an in-flight request on the server and stop
  // it — there's no such handle — it only clears the status; if that request
  // is in fact still running, it'll just overwrite this again once it
  // finishes on its own.
  async function cancelSync(provider: string) {
    setCancelingProvider(provider);
    setError(null);
    try {
      await apiPost(`/integrations/${provider}/cancel`);
      await load();
    } catch (err: any) {
      setError(err.message ?? "Could not cancel the sync.");
    } finally {
      setCancelingProvider(null);
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

  if (integrations === null) return <Loading />;

  return (
    <>
      <Section title="Sync health">
        <div className="grid sm:grid-cols-2 gap-3">
          <div className="card flex items-center gap-4">
            <Ring percent={powerschoolHealth.percent} tone={powerschoolHealth.tone} />
            <div className="min-w-0">
              <div className="font-medium text-sm">PowerSchool</div>
              <div className={`text-xs mt-0.5 ${powerschoolHealth.tone === "bad" ? "text-atlas-bad" : "text-atlas-muted"}`}>
                {powerschool ? powerschoolHealth.label : "Not connected"}
              </div>
            </div>
          </div>
          <div className="card flex items-center gap-4">
            <Ring percent={schoologyHealth.percent} tone={schoologyHealth.tone} />
            <div className="min-w-0">
              <div className="font-medium text-sm">Schoology</div>
              <div className={`text-xs mt-0.5 ${schoologyHealth.tone === "bad" ? "text-atlas-bad" : "text-atlas-muted"}`}>
                {schoology ? schoologyHealth.label : "Not connected"}
              </div>
            </div>
          </div>
        </div>

        {overdue.length > 0 && (
          <div className="card mt-3 border-atlas-bad/40 bg-atlas-bad/5">
            <div className="text-sm font-medium text-atlas-bad">
              {overdue.join(" and ")} {overdue.length > 1 ? "haven't" : "hasn't"} synced on schedule
            </div>
            <div className="text-xs text-atlas-muted mt-1">
              Expected roughly every 12 hours. If this persists, the scheduler that's supposed to
              trigger sync automatically may not be running — sync manually below in the meantime.
            </div>
          </div>
        )}
      </Section>

      <Section title="Grades & assignments">
        <div className="card">
          <div className="flex items-center justify-between gap-3">
            <div className="min-w-0">
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
                  {powerschool.status === "running" ? (
                    <button
                      className="btn-ghost"
                      title="Clears a stuck sync so you can try again. If it's genuinely still running in the background, it may briefly show as running again once it finishes."
                      disabled={cancelingProvider === "powerschool"}
                      onClick={() => cancelSync("powerschool")}
                    >
                      {cancelingProvider === "powerschool" ? "Canceling…" : "Cancel"}
                    </button>
                  ) : (
                    <button
                      className="btn-ghost"
                      disabled={syncingProvider === "powerschool"}
                      onClick={() => sync("powerschool")}
                    >
                      {syncingProvider === "powerschool" ? "Syncing…" : "Sync now"}
                    </button>
                  )}
                  <ActionMenu
                    items={[
                      { label: "Edit login", onClick: openConnectModal },
                      { label: debugging ? "Fetching…" : "Debug scrape", onClick: debugScrape, disabled: debugging },
                      { label: "Disconnect", onClick: () => disconnect("powerschool"), danger: true },
                    ]}
                  />
                </>
              ) : (
                <button className="btn-primary" onClick={openConnectModal}>
                  Connect
                </button>
              )}
            </div>
          </div>
        </div>

        {error && <div className="card mt-4 text-sm text-atlas-bad">{error}</div>}

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
            ) : lastResult.status === "running" ? (
              <>
                <div className="font-medium text-atlas-warn mb-1">Syncing…</div>
                <div className="text-atlas-muted">
                  {lastResult.courses ?? 0} course{lastResult.courses === 1 ? "" : "s"} processed so
                  far — a lot of courses can take a few passes like this to finish.
                </div>
              </>
            ) : (
              <div className="text-atlas-bad">{lastResult.detail ?? "Sync did not complete."}</div>
            )}
          </div>
        )}

        {debugError && <div className="card mt-4 text-sm text-atlas-bad">{debugError}</div>}
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
          <div className="flex items-center justify-between gap-3">
            <div className="min-w-0">
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
                  {schoology.status === "running" ? (
                    <button
                      className="btn-ghost"
                      title="Clears a stuck sync so you can try again. If it's genuinely still running in the background, it may briefly show as running again once it finishes."
                      disabled={cancelingProvider === "schoology"}
                      onClick={() => cancelSync("schoology")}
                    >
                      {cancelingProvider === "schoology" ? "Canceling…" : "Cancel"}
                    </button>
                  ) : (
                    <button
                      className="btn-ghost"
                      disabled={syncingProvider === "schoology"}
                      onClick={() => sync("schoology")}
                    >
                      {syncingProvider === "schoology" ? "Syncing…" : "Sync now"}
                    </button>
                  )}
                  <ActionMenu
                    items={[
                      { label: "Edit login", onClick: openSchoologyModal },
                      {
                        label: schoologyDebugging ? "Fetching…" : "Debug materials",
                        onClick: debugFetchSchoology,
                        disabled: schoologyDebugging,
                      },
                      { label: "Disconnect", onClick: () => disconnect("schoology"), danger: true },
                    ]}
                  />
                </>
              ) : (
                <button className="btn-primary" onClick={openSchoologyModal}>
                  Connect
                </button>
              )}
            </div>
          </div>
          {schoology && (
            <div className="mt-3 pt-3 border-t border-atlas-border flex items-center justify-between gap-3">
              <div className="min-w-0">
                <div className="text-sm font-medium">Google Drive</div>
                <div className="text-xs text-atlas-muted mt-0.5">
                  {schoology.config?.google_connected
                    ? "Connected — Google Docs/Slides/Sheets found in course materials download automatically."
                    : "Not connected — Google Docs found in your materials get flagged instead of downloaded."}
                </div>
              </div>
              <button
                className="btn-ghost shrink-0"
                disabled={googleConnecting || googleDisconnecting}
                onClick={schoology.config?.google_connected ? disconnectGoogle : connectGoogle}
              >
                {schoology.config?.google_connected
                  ? googleDisconnecting ? "Disconnecting…" : "Disconnect"
                  : googleConnecting ? "Connecting…" : "Connect Google Drive"}
              </button>
            </div>
          )}
          {googleStatus && (
            <div className={`text-xs mt-2 ${googleStatus.ok ? "text-atlas-good" : "text-atlas-bad"}`}>
              {googleStatus.text}
            </div>
          )}
          {schoology && (
            <div className="mt-3 pt-3 border-t border-atlas-border flex items-center gap-2">
              <input
                className="input text-xs"
                placeholder="Debug: search a course, e.g. AP Physics…"
                value={schoologyDebugQuery}
                onChange={(e) => setSchoologyDebugQuery(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && debugFetchSchoology()}
              />
            </div>
          )}
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
                      <div className="text-atlas-muted">No items found.</div>
                    ) : (
                      <ul className="space-y-1">
                        {p.items.map((item, i) => (
                          <li key={i} className="flex flex-wrap items-baseline gap-x-2">
                            <span>{item.folder ? `${item.folder} · ` : ""}{item.name}</span>
                            {item.type && <span className="text-atlas-muted">({item.type})</span>}
                            {item.downloaded === true && (
                              <span className="text-atlas-good">
                                ✓ downloaded ({item.download_content_type}, {item.download_size_bytes} bytes)
                              </span>
                            )}
                            {item.downloaded === false && (
                              <span className="text-atlas-bad">✗ couldn&apos;t find a real download</span>
                            )}
                            {item.download_error && (
                              <span className="text-atlas-bad">✗ download failed: {item.download_error}</span>
                            )}
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
                    {/* Every raw link the walk actually saw on each page it
                        visited, not just the ones classified as real
                        content above — shown unconditionally so nothing
                        is hidden behind the classifier's judgment call. */}
                    {p.walk_trace && p.walk_trace.length > 0 && (
                      <div className="mt-1.5 space-y-1">
                        {p.items.length === 0 && (
                          <div className="text-atlas-muted">{diagnoseWalk(p.walk_trace)}</div>
                        )}
                        <details>
                          <summary className="cursor-pointer text-atlas-muted">
                            All links seen, every page checked ({p.walk_trace.length})
                          </summary>
                          <ul className="mt-1 space-y-2 font-mono text-[11px] leading-tight">
                            {p.walk_trace.map((step, i) => (
                              <li key={i} className="break-all text-atlas-muted">
                                <div>
                                  [{step.status_code ?? "?"}]
                                  {step.looks_like_login ? " ⚠ login-wall" : ""}
                                  {step.likely_js_shell ? " ⚠ js-shell" : ""}
                                  {step.error ? " ⚠ login failed" : ""}{" "}
                                  {step.parsed_count ?? 0} of {step.raw_link_count ?? 0} links ·{" "}
                                  {step.final_url ?? step.requested_url}
                                </div>
                                {step.error && <div className="text-atlas-bad">{step.error}</div>}
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
              placeholder="https://yourdistrict.schoology.com"
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
    </>
  );
}
