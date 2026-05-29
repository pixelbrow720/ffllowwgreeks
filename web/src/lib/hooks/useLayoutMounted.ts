"use client";

import { useEffect, useState, useRef, type RefObject } from "react";

export interface MeasuredBox {
  ready: boolean;
  width: number;
  height: number;
}

// useMeasuredBox — observe the ref'd element and return its current
// width/height. Returns `ready=true` only after the element has measured
// a positive box. Recharts' ResponsiveContainer warns "width(-1) and
// height(-1)" on its first paint when the parent flex-child has not yet
// resolved a size; passing measured dimensions explicitly (instead of
// `width="100%" height="100%"`) eliminates the placeholder render.
export function useMeasuredBox<T extends HTMLElement>(
  ref: RefObject<T | null>,
): MeasuredBox {
  const [box, setBox] = useState<MeasuredBox>({ ready: false, width: 0, height: 0 });
  const lastRef = useRef<{ w: number; h: number }>({ w: 0, h: 0 });

  useEffect(() => {
    const el = ref.current;
    if (!el) return;

    const sync = () => {
      const r = el.getBoundingClientRect();
      const w = Math.floor(r.width);
      const h = Math.floor(r.height);
      if (w <= 0 || h <= 0) return;
      if (lastRef.current.w === w && lastRef.current.h === h) return;
      lastRef.current = { w, h };
      setBox({ ready: true, width: w, height: h });
    };

    sync();

    const ro = new ResizeObserver(() => {
      sync();
    });
    ro.observe(el);

    const raf = requestAnimationFrame(sync);

    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
    };
  }, [ref]);

  return box;
}
