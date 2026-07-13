"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useAuth } from "./AuthProvider";

// Monoline SVG marks — cleaner and more modern than glyph characters.
const Icon = ({ d }: { d: string }) => (
  <svg
    viewBox="0 0 24 24"
    className="w-[18px] h-[18px]"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.6"
    strokeLinecap="round"
    strokeLinejoin="round"
  >
    <path d={d} />
  </svg>
);

const NAV = [
  { href: "/", label: "Dashboard", d: "M4 13h6V4H4v9Zm0 7h6v-5H4v5Zm10 0h6V11h-6v9Zm0-16v5h6V4h-6Z" },
  { href: "/courses", label: "Courses", d: "M4 5h16M4 5v14M4 19h16M20 5v14M9 9h7M9 13h7" },
  { href: "/assignments", label: "Assignments", d: "M9 5h6M9 5a2 2 0 0 1 2-2h2a2 2 0 0 1 2 2M9 5H7a2 2 0 0 0-2 2v11a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2h-2M9 13l2 2 4-4" },
  { href: "/documents", label: "Documents", d: "M14 3v5h5M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8l-5-5Z" },
  { href: "/search", label: "Ask Atlas", d: "M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14ZM21 21l-4.3-4.3" },
  { href: "/knowledge", label: "Knowledge", d: "M12 3l2.5 5.5L20 11l-5.5 2.5L12 19l-2.5-5.5L4 11l5.5-2.5L12 3Z" },
  { href: "/analytics", label: "Analytics", d: "M4 20V4M4 20h16M8 16v-4M12 16V8M16 16v-6" },
];

export function Sidebar() {
  const path = usePathname();
  const { session, signOut } = useAuth();

  return (
    <aside className="w-60 shrink-0 border-r border-atlas-border bg-atlas-panel/60 backdrop-blur flex flex-col">
      <div className="px-5 py-6">
        <div className="text-xl font-display font-semibold tracking-tight flex items-center gap-2">
          <span className="grid place-items-center w-7 h-7 rounded-lg bg-atlas-accent text-atlas-bg text-sm font-bold">
            A
          </span>
          Atlas
        </div>
        <div className="text-[11px] text-atlas-muted mt-1 pl-9 -mt-1">Academic OS</div>
      </div>
      <nav className="flex-1 px-3 space-y-1">
        {NAV.map((n) => {
          const active = n.href === "/" ? path === "/" : path.startsWith(n.href);
          return (
            <Link
              key={n.href}
              href={n.href}
              className={`group relative flex items-center gap-3 px-3 py-2 rounded-xl text-sm transition-all ${
                active
                  ? "bg-atlas-accent/10 text-atlas-text"
                  : "text-atlas-muted hover:bg-atlas-panel2 hover:text-atlas-text"
              }`}
            >
              <span
                className={`absolute left-0 top-1/2 -translate-y-1/2 h-5 w-[3px] rounded-full transition-all ${
                  active ? "bg-atlas-accent" : "bg-transparent"
                }`}
              />
              <span className={active ? "text-atlas-accent" : "opacity-80"}>
                <Icon d={n.d} />
              </span>
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
