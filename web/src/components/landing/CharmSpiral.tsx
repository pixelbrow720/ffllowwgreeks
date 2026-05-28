"use client";

/**
 * SVG charm spiral — large decorative background element evoking the
 * intraday charm decay curve as a logarithmic spiral.
 */
export function CharmSpiral() {
  const arms = 5;
  const turns = 4;
  const points = 240;
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
    <div className="pointer-events-none absolute inset-0 -z-10 overflow-hidden">
      <svg
        viewBox="0 0 100 100"
        preserveAspectRatio="xMidYMid slice"
        className="absolute inset-0 h-[140%] w-[140%] -translate-x-[20%] -translate-y-[18%] animate-spin-slow"
      >
        <defs>
          <radialGradient id="spiralGlow" cx="50%" cy="50%" r="50%">
            <stop offset="0%" stopColor="#ff2a5b" stopOpacity="0" />
            <stop offset="55%" stopColor="#ff2a5b" stopOpacity="0.22" />
            <stop offset="100%" stopColor="#ff2a5b" stopOpacity="0" />
          </radialGradient>
        </defs>
        <circle cx={cx} cy={cy} r="42" fill="url(#spiralGlow)" />
        {paths.map((d, i) => (
          <path
            key={i}
            d={d}
            stroke="#ff2a5b"
            strokeOpacity={0.1 + (i / arms) * 0.08}
            strokeWidth="0.08"
            fill="none"
          />
        ))}
      </svg>

      {/* counter-rotating inner ring */}
      <svg
        viewBox="0 0 100 100"
        preserveAspectRatio="xMidYMid slice"
        className="absolute inset-0 h-[120%] w-[120%] -translate-x-[10%] -translate-y-[10%]"
        style={{ animation: "spin 28s linear reverse infinite" }}
      >
        {[18, 24, 30, 36, 42].map((r, i) => (
          <circle
            key={r}
            cx="50"
            cy="50"
            r={r}
            stroke="#ff2a5b"
            strokeOpacity={0.04 + i * 0.012}
            strokeWidth="0.08"
            fill="none"
            strokeDasharray="0.6 1.2"
          />
        ))}
        {Array.from({ length: 24 }).map((_, i) => {
          const a = (i / 24) * Math.PI * 2;
          const x1 = 50 + Math.cos(a) * 18;
          const y1 = 50 + Math.sin(a) * 18;
          const x2 = 50 + Math.cos(a) * 44;
          const y2 = 50 + Math.sin(a) * 44;
          return (
            <line
              key={i}
              x1={x1}
              y1={y1}
              x2={x2}
              y2={y2}
              stroke="#ff2a5b"
              strokeOpacity={0.04}
              strokeWidth="0.05"
            />
          );
        })}
      </svg>
    </div>
  );
}
