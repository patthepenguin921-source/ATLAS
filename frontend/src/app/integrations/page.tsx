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
}

interface SyncResult {
  status: string;
  courses?: number;
  assignments?: number;
  grades?: number;
  errors?: string[];
  detail?: string;
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
  const [form, setForm] = useState({ base_url: "", username: "", password: "" });

  async function load() {
    setIntegrations(await apiGet<Integration[]>("/integrations"));
  }
  useEffect(() => {
    load();
  }, []);

  const powerschool = integrations?.find((i) => i.provider === "powerschool");

  async function connect(e: React.FormEvent) {
    e.preventDefault();
    setConnecting(true);
    setError(null);
    setLastResult(null);
    try {
      const result = await apiPost<SyncResult>("/integrations/powerschool/connect", form);
      setLastResult(result);
      setConnectOpen(false);
      setForm({ base_url: "", username: "", password: "" });
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
        onClose={() => setConnectOpen(false)}
        title="Connect PowerSchool"
        footer={
          <>
            <button className="btn-ghost" onClick={() => setConnectOpen(false)}>Cancel</button>
            <button className="btn-primary" form="ps-connect-form" disabled={connecting}>
              {connecting ? "Connecting…" : "Connect"}
            </button>
          </>
        }
      >
        <form id="ps-connect-form" onSubmit={connect} className="space-y-3">
          <p className="text-sm text-atlas-muted">
            Atlas logs in the same way you do on the PowerSchool website. Your password is stored
            encrypted and only used to pull your grades.
          </p>
          {error && <div className="text-sm text-atlas-bad">{error}</div>}
          <div>
            <label className="label">Portal URL</label>
            <input
              className="input"
              required
              placeholder="https://yourdistrict.powerschool.com"
              value={form.base_url}
              onChange={(e) => setForm({ ...form, base_url: e.target.value })}
            />
          </div>
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
        </form>
      </Modal>
    </AppShell>
  );
}
