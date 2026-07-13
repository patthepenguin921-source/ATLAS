"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

// Agents were merged into "Ask Atlas" (the search tab). Keep this route alive
// for old links by redirecting.
export default function ChatRedirect() {
  const router = useRouter();
  useEffect(() => {
    router.replace("/search");
  }, [router]);
  return (
    <div className="min-h-screen grid place-items-center text-atlas-muted">
      Redirecting to Ask Atlas…
    </div>
  );
}
