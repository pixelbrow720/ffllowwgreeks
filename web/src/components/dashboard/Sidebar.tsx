"use client";

import { useState } from "react";
import {
  Activity,
  Bell,
  Calendar,
  Code2,
  Compass,
  Eye,
  FileCode2,
  Gauge,
  History,
  LayoutDashboard,
  LifeBuoy,
  Settings,
  TrendingUp,
  Webhook,
  Workflow,
} from "lucide-react";
import { cn } from "@/lib/utils";

const NAV = [
  {
    label: "Live",
    items: [
      { icon: LayoutDashboard, label: "Overview", active: true },
      { icon: Gauge, label: "DPI Console" },
      { icon: Compass, label: "Charm Clock" },
      { icon: TrendingUp, label: "Flow Tape" },
      { icon: Eye, label: "Walls & Levels" },
    ],
  },
  {
    label: "Research",
    items: [
      { icon: Workflow, label: "What-If Simulator" },
      { icon: History, label: "Replay" },
      { icon: Calendar, label: "Backtest" },
      { icon: Activity, label: "Signal Studio" },
    ],
  },
  {
    label: "Build",
    items: [
      { icon: Bell, label: "Alert Rules" },
      { icon: Webhook, label: "Webhooks" },
      { icon: Code2, label: "API Keys" },
      { icon: FileCode2, label: "OpenAPI" },
    ],
  },
];

export function Sidebar() {
  const [open, setOpen] = useState(false);

  return (
    <>
      {/* edge hover trigger — invisible 12px hot zone */}
      <div
        className="fixed left-0 top-0 bottom-0 z-40 w-3"
        onMouseEnter={() => setOpen(true)}
      />

      {/* always-visible mini rail */}
      <div
        className={cn(
          "fixed left-3 top-1/2 -translate-y-1/2 z-30 flex flex-col items-center gap-1 rounded-full border border-line/70 bg-bg-card/60 px-1 py-2 backdrop-blur-xl transition-opacity duration-300",
          open ? "opacity-0 pointer-events-none" : "opacity-100",
        )}
      >
        {NAV[0].items.slice(0, 5).map((item) => {
          const Icon = item.icon;
          return (
            <button
              key={item.label}
              className={cn(
                "flex h-8 w-8 items-center justify-center rounded-full transition-colors",
                item.active
                  ? "bg-brand-dim text-brand-hi"
                  : "text-ink-faint hover:bg-bg-hover hover:text-ink-base",
              )}
              title={item.label}
              onMouseEnter={() => setOpen(true)}
            >
              <Icon className="h-3.5 w-3.5" />
            </button>
          );
        })}
      </div>

      {/* full sidebar overlay — slides in from left */}
      <aside
        onMouseLeave={() => setOpen(false)}
        className={cn(
          "fixed left-3 top-3 bottom-3 z-40 w-[260px] flex flex-col gap-3 rounded-2xl border border-line/70",
          "bg-gradient-to-b from-bg-card/95 to-bg-card/85 backdrop-blur-xl",
          "shadow-[0_30px_120px_-30px_rgba(0,0,0,0.7)]",
          "transition-all duration-300 ease-[cubic-bezier(0.16,1,0.3,1)]",
          open
            ? "translate-x-0 opacity-100"
            : "-translate-x-[105%] opacity-0 pointer-events-none",
        )}
      >
        {/* brand block */}
        <div className="border-b border-line/50 p-4">
          <div className="flex items-center gap-2.5">
            <div className="relative h-7 w-7 shrink-0">
              <div className="absolute inset-0 rounded-md bg-gradient-to-br from-brand to-brand-lo" />
              <div className="absolute inset-[3px] rounded-[5px] bg-bg-base" />
              <div className="absolute inset-[6px] rounded-[3px] bg-gradient-to-br from-brand-hi to-brand" />
            </div>
            <div className="leading-tight min-w-0">
              <div className="text-[14px] font-semibold tracking-tight text-ink-high">
                flow<span className="text-brand">greeks</span>
              </div>
              <div className="text-[9px] uppercase tracking-[0.2em] text-ink-faint">
                read the dealer
              </div>
            </div>
          </div>

          <div className="mt-4 flex items-center gap-2.5 rounded-xl border border-line/60 bg-bg-base/40 px-2.5 py-2">
            <div className="flex h-7 w-7 items-center justify-center rounded-full bg-gradient-to-br from-brand/40 to-brand-lo/30 text-[11px] font-semibold text-ink-high">
              M
            </div>
            <div className="leading-tight min-w-0 flex-1">
              <div className="truncate text-[12.5px] text-ink-high">marko.k</div>
              <div className="flex items-center gap-1.5 text-[9.5px] uppercase tracking-[0.16em] text-signal-up">
                <span className="relative flex h-1.5 w-1.5">
                  <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-signal-up opacity-75" />
                  <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-signal-up" />
                </span>
                Pro · live
              </div>
            </div>
          </div>
        </div>

        {/* nav */}
        <nav className="flex-1 overflow-y-auto px-2.5 space-y-5 scrollbar-hide">
          {NAV.map((sec) => (
            <div key={sec.label}>
              <div className="px-2.5 pb-1.5 text-[9.5px] uppercase tracking-[0.22em] text-ink-faint">
                {sec.label}
              </div>
              <div className="space-y-0.5">
                {sec.items.map((item) => {
                  const Icon = item.icon;
                  return (
                    <button
                      key={item.label}
                      className={cn(
                        "group flex w-full items-center gap-2.5 rounded-xl px-2.5 py-2 text-left text-[12.5px] transition-all",
                        item.active
                          ? "bg-gradient-to-r from-brand-dim to-brand-dim/40 text-ink-high border border-brand/25 shadow-[0_0_24px_-8px_rgba(255,42,91,0.5)]"
                          : "text-ink-muted hover:bg-bg-hover/70 hover:text-ink-base border border-transparent",
                      )}
                    >
                      <Icon
                        className={cn(
                          "h-3.5 w-3.5",
                          item.active
                            ? "text-brand-hi"
                            : "text-ink-faint group-hover:text-ink-base",
                        )}
                      />
                      <span className="flex-1 truncate">{item.label}</span>
                      {item.active && (
                        <span className="h-1.5 w-1.5 rounded-full bg-brand shadow-[0_0_8px_#ff2a5b]" />
                      )}
                    </button>
                  );
                })}
              </div>
            </div>
          ))}
        </nav>

        {/* footer */}
        <div className="border-t border-line/50 p-2 space-y-0.5">
          <button className="flex w-full items-center gap-2.5 rounded-xl px-2.5 py-2 text-[12px] text-ink-muted hover:bg-bg-hover/70 hover:text-ink-base transition-colors">
            <Settings className="h-3.5 w-3.5" /> Settings
          </button>
          <button className="flex w-full items-center gap-2.5 rounded-xl px-2.5 py-2 text-[12px] text-ink-muted hover:bg-bg-hover/70 hover:text-ink-base transition-colors">
            <LifeBuoy className="h-3.5 w-3.5" /> Docs · /api
          </button>
        </div>

        {/* status block */}
        <div className="m-2 mt-0 rounded-xl border border-line/60 bg-bg-base/40 px-3.5 py-3">
          <div className="text-[9.5px] uppercase tracking-[0.2em] text-ink-faint">
            Pipeline · live
          </div>
          <div className="mt-2 space-y-1.5 text-[10.5px]">
            <Row label="WS lag" value="42 ms" tone="up" />
            <Row label="Feed" value="OPRA · live" tone="up" />
            <Row label="Snap age" value="0.4s" />
          </div>
        </div>
      </aside>
    </>
  );
}

function Row({
  label,
  value,
  tone = "default",
}: {
  label: string;
  value: string;
  tone?: "default" | "up";
}) {
  return (
    <div className="flex items-center justify-between text-ink-muted">
      <span>{label}</span>
      <span
        className={cn(
          "tabnum",
          tone === "up" ? "text-signal-up" : "text-ink-base",
        )}
      >
        {value}
      </span>
    </div>
  );
}
