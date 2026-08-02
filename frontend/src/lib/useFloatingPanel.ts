"use client";

import { useEffect, useLayoutEffect, useRef, useState } from "react";

export interface FloatingRect {
  x: number;
  y: number;
  w: number;
  h: number;
}

interface FloatingPanelOptions {
  open: boolean;
  defaultWidth: number;
  defaultHeight: number;
  minWidth?: number;
  minHeight?: number;
  // Where the panel starts each time it opens -- "center" of the viewport,
  // or pinned near a corner via marginX/marginY (e.g. the chat popup
  // starting right above its own toggle button).
  anchor?: "center" | "bottom-right";
  marginX?: number;
  marginY?: number;
}

function clamp(v: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, v));
}

/** Shared drag/resize behavior for a panel positioned by its own pixel rect
 *  instead of being centered/anchored by CSS -- backs both the shared
 *  Modal's draggable/resizable mode and the always-on FloatingChat popup,
 *  so "move/resize this panel anywhere on screen" has one implementation
 *  instead of drifting between the two places it's offered.
 *
 *  Re-anchors to its starting position/size every time `open` flips true,
 *  rather than remembering wherever it was left the last time it closed. */
export function useFloatingPanel({
  open, defaultWidth, defaultHeight, minWidth = 320, minHeight = 200,
  anchor = "center", marginX = 24, marginY = 24,
}: FloatingPanelOptions) {
  const [rect, setRect] = useState<FloatingRect | null>(null);
  const dragState = useRef<{ startX: number; startY: number; origX: number; origY: number } | null>(null);
  const resizeState = useRef<{ startX: number; startY: number; origW: number; origH: number } | null>(null);

  // useLayoutEffect (not useEffect) so `rect` is set before the first paint
  // -- otherwise the first open of the panel would briefly render at its
  // default CSS position and then jump once the rect commits.
  useLayoutEffect(() => {
    if (!open) return;
    const w = Math.min(defaultWidth, window.innerWidth - 32);
    const h = Math.min(defaultHeight, window.innerHeight - 32);
    const x = anchor === "center" ? Math.round((window.innerWidth - w) / 2) : window.innerWidth - w - marginX;
    const y = anchor === "center" ? Math.round((window.innerHeight - h) / 2) : window.innerHeight - h - marginY;
    setRect({ x, y, w, h });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  useEffect(() => {
    function onMove(e: PointerEvent) {
      setRect((r) => {
        if (!r) return r;
        if (dragState.current) {
          const d = dragState.current;
          return {
            ...r,
            x: clamp(d.origX + (e.clientX - d.startX), 0, window.innerWidth - 80),
            y: clamp(d.origY + (e.clientY - d.startY), 0, window.innerHeight - 40),
          };
        }
        if (resizeState.current) {
          const d = resizeState.current;
          return {
            ...r,
            w: clamp(d.origW + (e.clientX - d.startX), minWidth, window.innerWidth - r.x - 8),
            h: clamp(d.origH + (e.clientY - d.startY), minHeight, window.innerHeight - r.y - 8),
          };
        }
        return r;
      });
    }
    function onUp() {
      dragState.current = null;
      resizeState.current = null;
    }
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    return () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
    };
  }, [minWidth, minHeight]);

  function startDrag(e: { clientX: number; clientY: number }) {
    if (!rect) return;
    dragState.current = { startX: e.clientX, startY: e.clientY, origX: rect.x, origY: rect.y };
  }

  function startResize(e: { clientX: number; clientY: number }) {
    if (!rect) return;
    resizeState.current = { startX: e.clientX, startY: e.clientY, origW: rect.w, origH: rect.h };
  }

  return { rect, startDrag, startResize };
}
