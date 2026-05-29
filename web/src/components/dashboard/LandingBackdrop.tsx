"use client";

// LandingBackdrop — ambient atmospheric layer that ties the dashboard to
// the landing page. Renders behind every panel as a `position: absolute`
// `inset: 0` z-0 layer. The dashboard panels themselves keep their
// opaque `bg-bg-card` surface so the backdrop is felt at the seams (the
// 1px hairline gaps between panels), not underneath the data.
//
// Three stacked layers (replicates the landing-page motif at lower
// opacity):
//   - bg-grid-fine    — 24px hairline grid for spatial anchoring
//   - radial-brand    — single brand-pink lamp glow, top-left quadrant
//   - bg-noise        — film grain, mix-blend overlay
//
// Tuned to the dashboard density: ~30% of the landing-page intensity.
export function LandingBackdrop() {
  return (
    <div className="pointer-events-none fixed inset-0 z-0 overflow-hidden">
      <div className="absolute inset-0 bg-grid-fine opacity-[0.18]" />
      <div className="absolute inset-0 dashboard-radial-brand" />
      <div className="absolute inset-0 bg-noise opacity-[0.14] mix-blend-overlay" />
      <DashboardSpiral />
    </div>
  );
}

// DashboardSpiral — minimal echo of the landing CharmSpiral. Single
// faint arm, far less aggressive than the landing version. Pinned to the
// top-right outside the panel grid so it never sits behind a chart.
function DashboardSpiral() {
  const arms = 3;
  const turns = 3;
  const points = 160;
  const cx = 50;
  const cy = 50;
  const maxR = 46;

  const paths: string[] = [];
  for (let a = 0; a < arms; a++) {
    let d = "";
    for (let i = 0; i <= points; i++) {
      const t = i / points;
      const angle = t * turns * Math.PI * 2 + (a / arms) * Math.PI * 2;
      const r = Math.pow(t, 0.62) * maxR;
      const x = cx + Math.cos(angle) * r;
      const y = cy + Math.sin(angle) * r;
      d += `${i === 0 ? "M" : "L"} ${x.toFixed(3)} ${y.toFixed(3)} `;
    }
    paths.push(d);
  }

  return (
    <svg
      viewBox="0 0 100 100"
      preserveAspectRatio="xMidYMid slice"
      className="absolute -right-[28%] -top-[24%] h-[110%] w-[110%] animate-spin-slow opacity-[0.35]"
    >
      <defs>
        <radialGradient id="dashSpiralGlow" cx="50%" cy="50%" r="50%">
          <stop offset="0%" stopColor="#ff2a5b" stopOpacity="0" />
          <stop offset="50%" stopColor="#ff2a5b" stopOpacity="0.10" />
          <stop offset="100%" stopColor="#ff2a5b" stopOpacity="0" />
        </radialGradient>
      </defs>
      <circle cx={cx} cy={cy} r="42" fill="url(#dashSpiralGlow)" />
      {paths.map((d, i) => (
        <path
          key={i}
          d={d}
          stroke="#ff2a5b"
          strokeOpacity={0.05 + (i / arms) * 0.04}
          strokeWidth="0.06"
          fill="none"
        />
      ))}
    </svg>
  );
}
