import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./src/pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/components/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/app/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        bg: {
          base: "#08080a",
          card: "#0f0f12",
          hover: "#16161a",
          subtle: "#1c1c20",
        },
        line: {
          DEFAULT: "#26262a",
          strong: "#3a3a40",
        },
        ink: {
          high: "#f4f4f5",
          base: "#e4e4e7",
          muted: "#a1a1aa",
          faint: "#71717a",
          ghost: "#52525b",
        },
        signal: {
          up: "#22c55e",
          down: "#ef4444",
          warn: "#f59e0b",
          info: "#3b82f6",
          pin: "#a855f7",
        },
        brand: {
          DEFAULT: "#ff2a5b",
          hi: "#ff6f8d",
          lo: "#cc1e48",
          dim: "rgba(255,42,91,0.12)",
        },
      },
      fontFamily: {
        sans: ["var(--font-inter)", "system-ui", "sans-serif"],
        display: ["var(--font-inter)", "system-ui", "sans-serif"],
        mono: ["var(--font-jb-mono)", "ui-monospace", "monospace"],
      },
      fontSize: {
        "display-xl": ["clamp(3.5rem, 9vw, 9rem)", { lineHeight: "0.92", letterSpacing: "-0.04em", fontWeight: "600" }],
        "display-lg": ["clamp(2.5rem, 6vw, 5.5rem)", { lineHeight: "0.95", letterSpacing: "-0.035em", fontWeight: "600" }],
        "display-md": ["clamp(2rem, 4vw, 3.5rem)", { lineHeight: "1.02", letterSpacing: "-0.03em", fontWeight: "600" }],
      },
      animation: {
        marquee: "marquee 38s linear infinite",
        "marquee-rev": "marquee 48s linear infinite reverse",
        "pulse-slow": "pulse 4s cubic-bezier(0.4, 0, 0.6, 1) infinite",
        "spin-slow": "spin 14s linear infinite",
        flicker: "flicker 3s ease-in-out infinite",
      },
      keyframes: {
        marquee: {
          "0%": { transform: "translateX(0)" },
          "100%": { transform: "translateX(-50%)" },
        },
        flicker: {
          "0%, 100%": { opacity: "1" },
          "50%": { opacity: "0.55" },
        },
      },
    },
  },
  plugins: [],
};
export default config;
