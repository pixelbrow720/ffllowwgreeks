"use client";

import { useState } from "react";
import { RegimeStrip } from "@/components/dashboard/RegimeStrip";
import { RailNav } from "@/components/dashboard/RailNav";
import { LandingBackdrop } from "@/components/dashboard/LandingBackdrop";
import { DPILive } from "@/components/dashboard/DPILive";
import { SpotChart } from "@/components/dashboard/SpotChart";
import { GEXProfile } from "@/components/dashboard/GEXProfile";
import { KeyLevels } from "@/components/dashboard/KeyLevels";
import { PinPanel } from "@/components/dashboard/PinPanel";
import { DPITimelineLive } from "@/components/dashboard/DPITimelineLive";
import { SignalLog } from "@/components/dashboard/SignalLog";

type Sym = "SPX" | "NDX";

// Layout height budget at 1080p:
//   regime strip   56
//   hero row      720   (chart 460 + GEX 260 in main col, full-height side rails)
//   bottom strip  280
//   ----------------
//   total       1056   (4px slack)
//
// Outer container is `min-h-[1056px]` not `h-screen`, so smaller viewports
// scroll the body instead of squashing every panel into 0px. At 1080+ the
// whole dashboard fits without scroll.
//
// LandingBackdrop renders behind every panel as fixed inset-0; panels
// themselves keep an opaque bg-bg-card so the backdrop is felt at the
// 1px hairline seams between panels, never under chart ink.
export default function DashboardPage() {
  const [symbol, setSymbol] = useState<Sym>("SPX");

  return (
    <div className="relative min-h-[1056px] flex flex-col bg-bg-base text-ink-base">
      <LandingBackdrop />

      <div className="relative z-10 flex flex-col">
        <RegimeStrip symbol={symbol} onSymbolChange={setSymbol} />

        <div className="flex flex-1 pt-14">
          <RailNav />

          <main className="flex flex-1 min-w-0 flex-col">
            {/* Hero row — fixed 720px so the chart + GEX panels never bloat */}
            <div className="grid h-[720px] grid-cols-[280px_minmax(0,1fr)_360px]">
              <aside className="flex min-h-0 flex-col overflow-hidden border-r border-line">
                <DPILive symbol={symbol} />
              </aside>

              <section className="grid min-h-0 grid-rows-[460px_minmax(0,1fr)] overflow-hidden border-r border-line">
                <div className="min-h-0 overflow-hidden border-b border-line">
                  <SpotChart symbol={symbol} />
                </div>
                <div className="min-h-0 overflow-hidden">
                  <GEXProfile symbol={symbol} />
                </div>
              </section>

              <aside className="grid min-h-0 grid-rows-[260px_minmax(0,1fr)] overflow-hidden">
                <div className="min-h-0 overflow-hidden border-b border-line">
                  <KeyLevels symbol={symbol} />
                </div>
                <div className="min-h-0 overflow-hidden">
                  <PinPanel symbol={symbol} />
                </div>
              </aside>
            </div>

            {/* Bottom strip — DPI timeline + signal log, fixed 280 */}
            <div className="grid h-[280px] grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)] border-t border-line">
              <div className="min-h-0 overflow-hidden border-r border-line">
                <DPITimelineLive symbol={symbol} />
              </div>
              <div className="min-h-0 overflow-hidden">
                <SignalLog symbol={symbol} />
              </div>
            </div>
          </main>
        </div>
      </div>
    </div>
  );
}
