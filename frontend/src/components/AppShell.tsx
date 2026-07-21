"use client";

import { useEffect, useState } from "react";
import { useRouter, usePathname } from "next/navigation";
import { useAuth } from "./AuthProvider";
import { Sidebar } from "./Sidebar";
import { FloatingChat } from "./FloatingChat";

export function AppShell({
  title,
  subtitle,
  actions,
  children,
}: {
  title: string;
  subtitle?: string;
  actions?: React.ReactNode;
  children: React.ReactNode;
}) {
  const { session, loading } = useAuth();
  const router = useRouter();
  const pathname = usePathname();
  const [navOpen, setNavOpen] = useState(false);

  useEffect(() => {
    if (!loading && !session) router.replace("/login");
  }, [loading, session, router]);

  // Collapse the mobile drawer whenever the route changes.
  useEffect(() => {
    setNavOpen(false);
  }, [pathname]);

  if (loading)
    return (
      <div className="min-h-screen grid place-items-center text-atlas-muted">
        Loading Atlas…
      </div>
    );
  if (!session) return null;

  return (
    <div className="min-h-screen flex">
      <Sidebar open={navOpen} onClose={() => setNavOpen(false)} />
      <main className="flex-1 min-w-0 overflow-auto">
        <header className="sticky top-0 z-10 bg-atlas-bg/80 backdrop-blur border-b border-atlas-border px-4 sm:px-6 lg:px-8 py-4 lg:py-5 flex items-center justify-between gap-3">
          <div className="flex items-center gap-3 min-w-0">
            <button
              onClick={() => setNavOpen(true)}
              aria-label="Open menu"
              className="lg:hidden shrink-0 grid place-items-center w-9 h-9 rounded-lg border border-atlas-border text-atlas-muted hover:text-atlas-text hover:border-atlas-accent/40"
            >
              <svg viewBox="0 0 24 24" className="w-5 h-5" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
                <path d="M4 7h16M4 12h16M4 17h16" />
              </svg>
            </button>
            <div className="min-w-0">
              <h1 className="text-lg lg:text-xl font-semibold truncate">{title}</h1>
              {subtitle && <p className="text-sm text-atlas-muted mt-0.5 truncate">{subtitle}</p>}
            </div>
          </div>
          {actions && <div className="flex items-center gap-2 shrink-0">{actions}</div>}
        </header>
        <div className="p-4 sm:p-6 lg:p-8 max-w-6xl">{children}</div>
      </main>
      <FloatingChat />
    </div>
  );
}
