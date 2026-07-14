"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
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

  useEffect(() => {
    if (!loading && !session) router.replace("/login");
  }, [loading, session, router]);

  if (loading)
    return (
      <div className="min-h-screen grid place-items-center text-atlas-muted">
        Loading Atlas…
      </div>
    );
  if (!session) return null;

  return (
    <div className="min-h-screen flex">
      <Sidebar />
      <main className="flex-1 overflow-auto">
        <header className="sticky top-0 z-10 bg-atlas-bg/80 backdrop-blur border-b border-atlas-border px-8 py-5 flex items-center justify-between">
          <div>
            <h1 className="text-xl font-semibold">{title}</h1>
            {subtitle && <p className="text-sm text-atlas-muted mt-0.5">{subtitle}</p>}
          </div>
          <div className="flex items-center gap-2">{actions}</div>
        </header>
        <div className="p-8 max-w-6xl">{children}</div>
      </main>
      <FloatingChat />
    </div>
  );
}
