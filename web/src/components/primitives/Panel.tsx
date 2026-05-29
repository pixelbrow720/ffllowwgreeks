import { cn } from "@/lib/utils";
import type { ReactNode } from "react";

interface PanelProps {
  title?: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
  className?: string;
  contentClassName?: string;
  children: ReactNode;
  noPad?: boolean;
  tone?: "default" | "glass-brand" | "glass-warn";
}

// Panel — terminal-grade card primitive. Sharp corners, single hairline,
// no soft shadow. The header strip is a 28px monospace caption that does
// not compete with the data inside.
//
// Tone variants:
//   default     — opaque bg-bg-card, hairline border. Use for >90% of panels.
//   glass-brand — focal moment: brand hairline + subtle inward glow.
//                 Reserve for hero number callouts (DPI when FORCED).
//   glass-warn  — secondary focal: amber. Reserve for PinPanel when HOT.
//
// The glass tones intentionally LIFT the panel surface above the
// dashboard backdrop. Per CLAUDE.md, brand pink is decorative ambient
// only — these tones live in the *chrome*, not in the data ink.
export function Panel({
  title,
  subtitle,
  actions,
  className,
  contentClassName,
  children,
  noPad = false,
  tone = "default",
}: PanelProps) {
  const toneCls =
    tone === "glass-brand"
      ? "glass-brand backdrop-blur-xl"
      : tone === "glass-warn"
        ? "glass-warn backdrop-blur-xl"
        : "bg-bg-card";

  return (
    <div
      className={cn(
        "relative flex h-full min-h-0 flex-col overflow-hidden border border-line",
        toneCls,
        className,
      )}
    >
      {(title || actions) && (
        <header className="flex h-7 shrink-0 items-center justify-between border-b border-line/70 px-3">
          <div className="flex min-w-0 items-baseline gap-2">
            {title && (
              <h3 className="font-mono text-[10px] uppercase tracking-[0.2em] text-ink-muted">
                {title}
              </h3>
            )}
            {subtitle && (
              <p className="truncate text-[10.5px] text-ink-faint">{subtitle}</p>
            )}
          </div>
          {actions && <div className="flex items-center gap-2">{actions}</div>}
        </header>
      )}
      <div
        className={cn(
          noPad ? "" : "p-3",
          "min-h-0 flex-1",
          contentClassName,
        )}
      >
        {children}
      </div>
    </div>
  );
}

interface StatProps {
  label: string;
  value: ReactNode;
  delta?: ReactNode;
  hint?: ReactNode;
  accent?: "default" | "up" | "down" | "warn";
  className?: string;
}

const accentColors = {
  default: "text-ink-high",
  up: "text-accent-long",
  down: "text-accent-short",
  warn: "text-accent-warn",
};

export function Stat({ label, value, delta, hint, accent = "default", className }: StatProps) {
  return (
    <div className={cn("flex flex-col gap-1", className)}>
      <span className="font-mono text-[9.5px] uppercase tracking-[0.2em] text-ink-faint">
        {label}
      </span>
      <div className="flex items-baseline gap-2">
        <span className={cn("tabnum text-2xl font-medium leading-none", accentColors[accent])}>
          {value}
        </span>
        {delta && <span className="tabnum text-xs text-ink-muted">{delta}</span>}
      </div>
      {hint && <span className="text-[10.5px] text-ink-faint">{hint}</span>}
    </div>
  );
}

export function Pill({
  children,
  tone = "neutral",
  className,
}: {
  children: ReactNode;
  tone?: "neutral" | "up" | "down" | "warn";
  className?: string;
}) {
  // Pills are intentionally limited to the three earned accents + neutral.
  // No brand pink, no info blue, no pin violet — see CLAUDE.md color rule.
  const toneCls: Record<string, string> = {
    neutral: "border-line text-ink-muted bg-bg-subtle/40",
    up: "border-accent-long/30 text-accent-long bg-accent-long/10",
    down: "border-accent-short/30 text-accent-short bg-accent-short/10",
    warn: "border-accent-warn/30 text-accent-warn bg-accent-warn/10",
  };
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 border px-1.5 py-px font-mono text-[9.5px] font-medium uppercase tracking-[0.16em]",
        toneCls[tone],
        className,
      )}
    >
      {children}
    </span>
  );
}
