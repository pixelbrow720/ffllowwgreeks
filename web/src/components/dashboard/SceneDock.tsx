"use client";

import { ChevronLeft, ChevronRight } from "lucide-react";
import { cn } from "@/lib/utils";

export interface Scene {
  id: string;
  label: string;
  hint: string;
}

export function SceneDock({
  scenes,
  current,
  onChange,
}: {
  scenes: Scene[];
  current: number;
  onChange: (i: number) => void;
}) {
  const prev = () => onChange((current - 1 + scenes.length) % scenes.length);
  const next = () => onChange((current + 1) % scenes.length);

  return (
    <div className="fixed bottom-5 left-1/2 z-30 -translate-x-1/2">
      <div className="flex items-center gap-1.5 rounded-full border border-line/70 bg-bg-card/70 backdrop-blur-xl px-2 py-2 shadow-[0_30px_80px_-20px_rgba(0,0,0,0.8)]">
        <button
          onClick={prev}
          aria-label="Previous scene"
          className="flex h-8 w-8 items-center justify-center rounded-full text-ink-muted hover:bg-bg-hover hover:text-ink-base transition-colors"
        >
          <ChevronLeft className="h-3.5 w-3.5" />
        </button>

        <div className="mx-1 flex items-center gap-1">
          {scenes.map((s, i) => {
            const active = i === current;
            return (
              <button
                key={s.id}
                onClick={() => onChange(i)}
                className={cn(
                  "group relative flex items-center gap-2 rounded-full px-3 py-1.5 transition-all",
                  active
                    ? "bg-gradient-to-b from-brand-dim to-brand-dim/60 border border-brand/40 shadow-[0_0_24px_-6px_rgba(255,42,91,0.5)]"
                    : "border border-transparent hover:bg-bg-hover/70",
                )}
              >
                <span
                  className={cn(
                    "tabnum text-[10px] uppercase tracking-[0.18em] font-mono",
                    active ? "text-brand-hi" : "text-ink-faint",
                  )}
                >
                  0{i + 1}
                </span>
                <span
                  className={cn(
                    "text-[12px] font-medium",
                    active ? "text-ink-high" : "text-ink-muted group-hover:text-ink-base",
                  )}
                >
                  {s.label}
                </span>
              </button>
            );
          })}
        </div>

        <button
          onClick={next}
          aria-label="Next scene"
          className="flex h-8 w-8 items-center justify-center rounded-full bg-brand text-white hover:bg-brand-hi transition-colors shadow-[0_0_24px_-6px_#ff2a5b]"
        >
          <ChevronRight className="h-3.5 w-3.5" />
        </button>
      </div>

      {/* keyboard hint */}
      <div className="mt-2 flex items-center justify-center gap-3 text-[10px] uppercase tracking-[0.18em] text-ink-faint">
        <span className="flex items-center gap-1">
          <kbd className="rounded border border-line/60 bg-bg-card px-1.5 py-0.5 font-mono">←</kbd>
          <kbd className="rounded border border-line/60 bg-bg-card px-1.5 py-0.5 font-mono">→</kbd>
          switch
        </span>
        <span className="text-ink-ghost">·</span>
        <span>{scenes[current].hint}</span>
      </div>
    </div>
  );
}
