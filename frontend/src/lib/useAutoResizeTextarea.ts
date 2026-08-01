"use client";

import { useEffect, useRef } from "react";

/** Grows a <textarea> to fit its content up to its CSS max-height (where
 *  overflow-y takes over), instead of staying pinned to a single row while
 *  multi-line text scrolls invisibly inside it. */
export function useAutoResizeTextarea(value: string) {
  const ref = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight}px`;
  }, [value]);
  return ref;
}
