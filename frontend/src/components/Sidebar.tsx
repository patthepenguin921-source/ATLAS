"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useAuth } from "./AuthProvider";
import { LogoMark } from "./Logo";

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
  { href: "/integrations", label: "Integrations", d: "M8 12h8M8 12a4 4 0 1 1 0-8h1M8 12a4 4 0 1 0 0 8h1M16 12a4 4 0 1 0 0-8h-1M16 12a4 4 0 1 1 0 8h-1" },
];

export function Sidebar({
  open = false,
  onClose,
}: {
  /** Whether the off-canvas drawer is open. Ignored at the `lg` breakpoint and up,
   *  where the sidebar is always visible as a static column. */
  open?: boolean;
  onClose?: () => void;
}) {
  const path = usePathname();
  const { session, signOut } = useAuth();

  return (
    <>
      {open && (
        <div
          className="fixed inset-0 z-40 bg-black/60 backdrop-blur-sm lg:hidden"
          onClick={onClose}
        />
      )}
      <aside
        className={`fixed inset-y-0 left-0 z-50 w-64 shrink-0 border-r border-atlas-border
          bg-atlas-panel flex flex-col transition-transform duration-200 ease-out
          lg:static lg:z-auto lg:w-60 lg:translate-x-0 lg:bg-atlas-panel/60 lg:backdrop-blur
          ${open ? "translate-x-0" : "-translate-x-full"}`}
      >
        <div className="px-5 py-6 flex items-center justify-between">
          <div>
            <div className="text-xl font-display font-semibold tracking-tight flex items-center gap-2">
              <span className="grid place-items-center w-7 h-7 rounded-lg bg-atlas-accent text-white">
                <LogoMark className="w-4 h-4" bg="#6a8bff" />
              </span>
              Atlas
            </div>
            <div className="text-[11px] text-atlas-muted mt-1 pl-9">Academic OS</div>
          </div>
          <button
            onClick={onClose}
            aria-label="Close menu"
            className="lg:hidden text-atlas-muted hover:text-atlas-text text-xl leading-none px-1"
          >
            ×
          </button>
        </div>
        <nav className="flex-1 px-3 space-y-1 overflow-y-auto">
          {NAV.map((n) => {
            const active = n.href === "/" ? path === "/" : path.startsWith(n.href);
            return (
              <Link
                key={n.href}
                href={n.href}
                onClick={onClose}
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
          {(process.env.NEXT_PUBLIC_PR_NUMBER || process.env.NEXT_PUBLIC_COMMIT_SHA) && (
            <div className="text-[10px] text-atlas-muted/60 text-center mt-2">
              {process.env.NEXT_PUBLIC_PR_NUMBER
                ? `PR #${process.env.NEXT_PUBLIC_PR_NUMBER}`
                : process.env.NEXT_PUBLIC_COMMIT_SHA}
            </div>
          )}
        </div>
      </aside>
    </>
  );
}
