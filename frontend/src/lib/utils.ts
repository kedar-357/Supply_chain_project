import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

const inr0 = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 0 });
const num0 = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 });
const num1 = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 1 });

export const fmt = {
  inr: (v: number | null | undefined) => (v == null ? "–" : inr0.format(v)),
  /** Compact INR: ₹2.4 Cr, ₹12.5 L, ₹42.0 K */
  inrCompact: (v: number | null | undefined) => {
    if (v == null) return "–";
    const a = Math.abs(v);
    const s = v < 0 ? "-" : "";
    if (a >= 1e7) return `${s}₹${(a / 1e7).toFixed(2)} Cr`;
    if (a >= 1e5) return `${s}₹${(a / 1e5).toFixed(1)} L`;
    if (a >= 1e3) return `${s}₹${(a / 1e3).toFixed(1)} K`;
    return `${s}₹${a.toFixed(0)}`;
  },
  num: (v: number | null | undefined) => (v == null ? "–" : num0.format(v)),
  num1: (v: number | null | undefined) => (v == null ? "–" : num1.format(v)),
  pct: (v: number | null | undefined, digits = 1) => (v == null ? "–" : `${v.toFixed(digits)}%`),
  ratioPct: (v: number | null | undefined, digits = 0) => (v == null ? "–" : `${(v * 100).toFixed(digits)}%`),
  date: (v: string | null | undefined) =>
    v ? new Date(v).toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" }) : "–",
  dateShort: (v: string) => new Date(v).toLocaleDateString("en-IN", { day: "2-digit", month: "short" }),
  datetime: (v: string | null | undefined) =>
    v ? new Date(v).toLocaleString("en-IN", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }) : "–",
};

/** Categorical series colours (validated palette, fixed order — never cycled). */
export const SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"];
/** Status colours are reserved for state and always shown with a text label. */
export const STATUS = { good: "#15803d", warning: "#b45309", critical: "#b91c1c", info: "#2563eb", neutral: "#6b7280" };
export const GRID = "#e5e7eb";

export const STATUS_LABEL: Record<string, string> = {
  healthy: "Healthy",
  low: "Low stock",
  critical: "Critical",
  out_of_stock: "Out of stock",
  overstock: "Overstock",
};
