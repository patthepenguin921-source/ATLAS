"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

// Integrations moved into Settings (see /settings's "Integrations" tab) —
// this route stays only so old links/bookmarks still land somewhere.
export default function IntegrationsRedirect() {
  const router = useRouter();
  useEffect(() => {
    router.replace("/settings?tab=Integrations");
  }, [router]);
  return null;
}
