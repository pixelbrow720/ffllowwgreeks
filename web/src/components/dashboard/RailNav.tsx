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

// RailNav — slim, static, monochrome icon rail. 56px wide, always
// visible. The active item gets a brand-tinted glass pill (decorative
// ambient, never on data) plus a 2px brand accent strip.
//
// Adopted from 21st.dev/community/docks: hover-lift with tooltip-like
// label slide-in is tempting but breaks the "no peekaboo" rule from R1
// (a 0DTE trader should not be playing hide-and-seek with their nav).
// We keep nav fully visible; the dock pattern only inspires the active-
// pill chrome.
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
    <nav className="relative flex w-14 shrink-0 flex-col items-stretch border-r border-line bg-bg-base/80 backdrop-blur">
      <div className="flex flex-col items-stretch gap-px py-2">
        {NAV.map((item) => {
          const Icon = item.icon;
          return (
            <button
              key={item.label}
              title={item.pending ? `${item.label} · soon` : item.label}
              disabled={item.pending}
              className={cn(
                "group relative flex h-9 items-center justify-center transition-all duration-200",
                item.active
                  ? "text-ink-high"
                  : item.pending
                    ? "cursor-not-allowed text-ink-ghost"
                    : "text-ink-faint hover:bg-bg-hover hover:text-ink-base",
              )}
            >
              {item.active && (
                <>
                  <span className="absolute inset-y-0 left-0 w-[2px] bg-brand shadow-[0_0_8px_rgba(255,42,91,0.6)]" />
                  <span className="absolute inset-1 rounded-sm border border-brand/20 bg-brand/[0.06]" />
                </>
              )}
              <Icon className="relative h-4 w-4" strokeWidth={1.5} />
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
