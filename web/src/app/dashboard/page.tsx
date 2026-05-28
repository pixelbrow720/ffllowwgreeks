"use client";

import { useEffect, useState } from "react";
import { Sidebar } from "@/components/dashboard/Sidebar";
import { Topbar } from "@/components/dashboard/Topbar";
import { SceneDock, type Scene } from "@/components/dashboard/SceneDock";
import { DPIGauge } from "@/components/dashboard/DPIGauge";
import { CharmClock } from "@/components/dashboard/CharmClock";
import { SpotChart } from "@/components/dashboard/SpotChart";
import { DPITimeline } from "@/components/dashboard/DPITimeline";
import { KeyLevels } from "@/components/dashboard/KeyLevels";
import { FlowTape } from "@/components/dashboard/FlowTape";
import { GEXProfile } from "@/components/dashboard/GEXProfile";
import { SignalLog } from "@/components/dashboard/SignalLog";
import { ForcedFlow } from "@/components/dashboard/ForcedFlow";

const SCENES: Scene[] = [
  { id: "pulse", label: "Pulse", hint: "Spot · DPI · Charm · Timeline" },
  { id: "levels", label: "Levels", hint: "GEX · Walls · Forced flow" },
  { id: "tape", label: "Tape", hint: "Flow · Signals" },
];

export default function DashboardPage() {
  const [scene, setScene] = useState(0);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA") return;
      if (e.key === "ArrowRight") setScene((s) => (s + 1) % SCENES.length);
      else if (e.key === "ArrowLeft") setScene((s) => (s - 1 + SCENES.length) % SCENES.length);
      else if (e.key === "1") setScene(0);
      else if (e.key === "2") setScene(1);
      else if (e.key === "3") setScene(2);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  return (
    <div className="relative h-screen w-screen overflow-hidden bg-bg-base text-ink-base">
      {/* ambient lamp glow */}
      <div className="pointer-events-none fixed -top-40 left-1/4 h-[400px] w-[640px] -translate-x-1/2 rounded-[100%] bg-brand/[0.06] blur-3xl" />
      <div className="pointer-events-none fixed top-0 inset-x-0 h-[500px] bg-grid opacity-[0.35] [mask-image:radial-gradient(ellipse_50%_50%_at_50%_0%,black,transparent_70%)]" />

      <Sidebar />
      <Topbar />

      {/* horizontal slider track — 3 scenes × 100vw */}
      <div
        className="flex h-full w-[300vw] transition-transform duration-700 ease-[cubic-bezier(0.16,1,0.3,1)]"
        style={{ transform: `translate3d(-${scene * 100}vw, 0, 0)` }}
      >
        <ScenePulse />
        <SceneLevels />
        <SceneTape />
      </div>

      <SceneDock scenes={SCENES} current={scene} onChange={setScene} />
    </div>
  );
}

function ScenePulse() {
  return (
    <section className="relative h-full w-screen px-6 pt-20 pb-28">
      <div className="mx-auto h-full max-w-[1600px] grid grid-cols-12 grid-rows-[minmax(0,3fr)_minmax(0,2fr)] gap-4">
        <div className="col-span-12 xl:col-span-8 row-span-1 min-h-0">
          <div className="h-full"><SpotChart /></div>
        </div>
        <div className="col-span-12 xl:col-span-4 row-span-1 min-h-0">
          <div className="h-full"><DPIGauge /></div>
        </div>
        <div className="col-span-12 xl:col-span-7 row-span-1 min-h-0">
          <div className="h-full"><CharmClock /></div>
        </div>
        <div className="col-span-12 xl:col-span-5 row-span-1 min-h-0">
          <div className="h-full"><DPITimeline /></div>
        </div>
      </div>
    </section>
  );
}

function SceneLevels() {
  return (
    <section className="relative h-full w-screen px-6 pt-20 pb-28">
      <div className="mx-auto h-full max-w-[1600px] grid grid-cols-12 grid-rows-[minmax(0,1fr)] gap-4">
        <div className="col-span-12 xl:col-span-7 min-h-0">
          <div className="h-full"><GEXProfile /></div>
        </div>
        <div className="col-span-12 xl:col-span-5 min-h-0 grid grid-rows-2 gap-4">
          <div className="min-h-0"><KeyLevels /></div>
          <div className="min-h-0"><ForcedFlow /></div>
        </div>
      </div>
    </section>
  );
}

function SceneTape() {
  return (
    <section className="relative h-full w-screen px-6 pt-20 pb-28">
      <div className="mx-auto h-full max-w-[1600px] grid grid-cols-12 gap-4">
        <div className="col-span-12 xl:col-span-8 min-h-0">
          <div className="h-full"><FlowTape /></div>
        </div>
        <div className="col-span-12 xl:col-span-4 min-h-0">
          <div className="h-full"><SignalLog /></div>
        </div>
      </div>
    </section>
  );
}
