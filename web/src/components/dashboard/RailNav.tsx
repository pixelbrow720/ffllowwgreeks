"use client";

import {
  Activity,
  Bell,
  Code2,
  Compass,
  Eye,
  Gauge,
  History,
  LayoutDashboard,
  Settings,
  TrendingUp,
  Webhook,
  Workflow,
} from "lucide-react";
import { cn } from "@/lib/utils";

// RailNav — slim, static, monochrome icon rail. Always visible at 56px
// wide. No hover-reveal, no brand-pink glows. Items pointing at unwired
// routes are dimmed.
const NAV: { icon: typeof LayoutDashboard; label: string; active?: boolean; pending?: boolean }[] = [
  { icon: LayoutDashboard, label: "Overview", active: true },
  { icon: Gauge, label: "DPI Console", pending: true },
  { icon: Compass, label: "Charm Clock", pending: true },
  { icon: TrendingUp, label: "Flow Tape", pending: true },
  { icon: Eye, label: "Walls & Levels", pending: true },
  { icon: Workflow, label: "Simulator", pending: true },
  { icon: History, label: "Replay", pending: true },
  { icon: Activity, label: "Backtest", pending: true },
  { icon: Bell, label: "Alert Rules", pending: true },
  { icon: Webhook, label: "Webhooks", pending: true },
  { icon: Code2, label: "API Keys", pending: true },
];

export function RailNav() {
  return (
    <nav className="flex w-14 shrink-0 flex-col items-stretch border-r border-line bg-bg-base">
      <div className="flex flex-col items-stretch gap-px py-2">
        {NAV.map((item) => {
          const Icon = item.icon;
          return (
            <button
              key={item.label}
              title={item.pending ? `${item.label} · soon` : item.label}
              disabled={item.pending}
              className={cn(
                "flex h-9 items-center justify-center transition-colors",
                item.active
                  ? "border-l-2 border-accent-long bg-bg-card text-ink-high"
                  : item.pending
                    ? "cursor-not-allowed text-ink-ghost"
                    : "text-ink-faint hover:bg-bg-hover hover:text-ink-base",
              )}
            >
              <Icon className="h-4 w-4" strokeWidth={1.5} />
            </button>
          );
        })}
      </div>

      <div className="mt-auto flex flex-col items-stretch gap-px border-t border-line py-2">
        <button
          title="Settings"
          className="flex h-9 items-center justify-center text-ink-faint transition-colors hover:bg-bg-hover hover:text-ink-base"
        >
          <Settings className="h-4 w-4" strokeWidth={1.5} />
        </button>
      </div>
    </nav>
  );
}
