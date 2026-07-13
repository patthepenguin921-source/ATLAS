"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useAuth } from "./AuthProvider";

const NAV = [
  { href: "/", label: "Dashboard", icon: "◧" },
  { href: "/courses", label: "Courses", icon: "▤" },
  { href: "/assignments", label: "Assignments", icon: "✎" },
  { href: "/documents", label: "Documents", icon: "▣" },
  { href: "/search", label: "Search", icon: "⌕" },
  { href: "/knowledge", label: "Knowledge", icon: "❈" },
  { href: "/analytics", label: "Analytics", icon: "◉" },
  { href: "/chat", label: "Agents", icon: "✦" },
];

export function Sidebar() {
  const path = usePathname();
  const { session, signOut } = useAuth();

  return (
    <aside className="w-56 shrink-0 border-r border-atlas-border bg-atlas-panel flex flex-col">
      <div className="px-5 py-5">
        <div className="text-lg font-semibold tracking-tight">
          <span className="text-atlas-accent">A</span>tlas
        </div>
        <div className="text-[11px] text-atlas-muted">Academic OS</div>
      </div>
      <nav className="flex-1 px-2 space-y-0.5">
        {NAV.map((n) => {
          const active = n.href === "/" ? path === "/" : path.startsWith(n.href);
          return (
            <Link
              key={n.href}
              href={n.href}
              className={`flex items-center gap-3 px-3 py-2 rounded-lg text-sm transition-colors ${
                active
                  ? "bg-atlas-accent/15 text-atlas-text"
                  : "text-atlas-muted hover:bg-atlas-panel2 hover:text-atlas-text"
              }`}
            >
              <span className="w-4 text-center opacity-80">{n.icon}</span>
              {n.label}
            </Link>
          );
        })}
      </nav>
      <div className="p-3 border-t border-atlas-border">
        <div className="text-xs text-atlas-muted truncate mb-2">
          {session?.user?.email ?? "Signed in"}
        </div>
        <button onClick={signOut} className="btn-ghost w-full text-xs py-1.5">
          Sign out
        </button>
      </div>
    </aside>
  );
}
