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

export function signColor(n: number) {
  if (n > 0) return "text-accent-long";
  if (n < 0) return "text-accent-short";
  return "text-ink-muted";
}
