import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function fmtNum(n: number, opts?: Intl.NumberFormatOptions) {
  return new Intl.NumberFormat("en-US", opts).format(n);
}

export function fmtUsd(n: number, compact = false) {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: compact ? 1 : 0,
    notation: compact ? "compact" : "standard",
  }).format(n);
}

export function fmtPct(n: number, digits = 1) {
  return `${n >= 0 ? "+" : ""}${n.toFixed(digits)}%`;
}

// fmtSignedAbbr — "+$1.84B" / "−$420M". Trader-readable for forced-flow
// notional and net GEX columns. Uses the proper minus glyph so signs line
// up under tabular nums.
export function fmtSignedAbbr(n: number, digits = 2): string {
  if (!Number.isFinite(n)) return "—";
  const abs = Math.abs(n);
  const sign = n >= 0 ? "+" : "\u2212";
  if (abs >= 1e9) return `${sign}$${(abs / 1e9).toFixed(digits)}B`;
  if (abs >= 1e6) return `${sign}$${(abs / 1e6).toFixed(digits === 2 ? 0 : digits)}M`;
  if (abs >= 1e3) return `${sign}$${(abs / 1e3).toFixed(0)}K`;
  return `${sign}$${abs.toFixed(0)}`;
}

// fmtSignedNum — same shape as above without the dollar prefix, for raw
// deltas like price moves.
export function fmtSignedNum(n: number, digits = 2): string {
  if (!Number.isFinite(n)) return "—";
  const sign = n >= 0 ? "+" : "\u2212";
  return `${sign}${Math.abs(n).toFixed(digits)}`;
}

export function signColor(n: number) {
  if (n > 0) return "text-accent-long";
  if (n < 0) return "text-accent-short";
  return "text-ink-muted";
}
