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
}

export function Panel({
  title,
  subtitle,
  actions,
  className,
  contentClassName,
  children,
  noPad = false,
}: PanelProps) {
  return (
    <div
      className={cn(
        "group/panel relative rounded-2xl border border-line/70",
        "bg-gradient-to-b from-bg-card/95 to-bg-card/60 backdrop-blur-sm",
        "shadow-[0_1px_0_0_rgba(255,255,255,0.04)_inset,0_30px_60px_-30px_rgba(0,0,0,0.6)]",
        "flex flex-col overflow-hidden transition-colors",
        "hover:border-line",
        className,
      )}
    >
      {(title || actions) && (
        <header className="flex items-center justify-between border-b border-line/50 px-5 py-3">
          <div className="min-w-0">
            {title && (
              <h3 className="text-[10.5px] font-medium uppercase tracking-[0.18em] text-ink-muted">
                {title}
              </h3>
            )}
            {subtitle && (
              <p className="mt-0.5 text-xs text-ink-faint">{subtitle}</p>
            )}
          </div>
          {actions && <div className="flex items-center gap-1.5">{actions}</div>}
        </header>
      )}
      <div className={cn(noPad ? "" : "p-5", "flex-1 min-h-0", contentClassName)}>
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
  accent?: "default" | "up" | "down" | "warn" | "brand";
  className?: string;
}

const accentColors = {
  default: "text-ink-high",
  up: "text-signal-up",
  down: "text-signal-down",
  warn: "text-signal-warn",
  brand: "text-brand-hi",
};

export function Stat({ label, value, delta, hint, accent = "default", className }: StatProps) {
  return (
    <div className={cn("flex flex-col gap-1", className)}>
      <span className="text-[10px] uppercase tracking-[0.18em] text-ink-faint">{label}</span>
      <div className="flex items-baseline gap-2">
        <span className={cn("tabnum text-2xl font-medium leading-none", accentColors[accent])}>
          {value}
        </span>
        {delta && <span className="tabnum text-xs text-ink-muted">{delta}</span>}
      </div>
      {hint && <span className="text-[11px] text-ink-faint">{hint}</span>}
    </div>
  );
}

export function Pill({
  children,
  tone = "neutral",
  className,
}: {
  children: ReactNode;
  tone?: "neutral" | "brand" | "up" | "down" | "warn" | "info" | "pin";
  className?: string;
}) {
  const toneCls: Record<string, string> = {
    neutral: "border-line/70 text-ink-base bg-bg-subtle/50",
    brand: "border-brand/40 text-brand-hi bg-brand-dim",
    up: "border-signal-up/30 text-signal-up bg-signal-up/10",
    down: "border-signal-down/30 text-signal-down bg-signal-down/10",
    warn: "border-signal-warn/30 text-signal-warn bg-signal-warn/10",
    info: "border-signal-info/30 text-signal-info bg-signal-info/10",
    pin: "border-signal-pin/30 text-signal-pin bg-signal-pin/10",
  };
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-[10px] font-medium uppercase tracking-[0.12em]",
        toneCls[tone],
        className,
      )}
    >
      {children}
    </span>
  );
}
