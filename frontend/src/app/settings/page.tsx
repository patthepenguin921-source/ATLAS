"use client";

import { useEffect, useState } from "react";
import { AppShell } from "@/components/AppShell";
import { Empty, Loading, Section } from "@/components/ui";
import { apiGet, apiPatch, apiDelete } from "@/lib/api";

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

const TABS = ["Account", "Memory"] as const;
type Tab = (typeof TABS)[number];

export default function SettingsPage() {
  const [tab, setTab] = useState<Tab>("Account");

  return (
    <AppShell title="Settings" subtitle="Your account and what Atlas remembers about you">
      <div className="flex gap-1 mb-6 border-b border-atlas-border">
        {TABS.map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
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
      {tab === "Account" ? <AccountTab /> : <MemoryTab />}
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
