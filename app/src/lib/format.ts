// Small presentation helpers shared across screens. No data access here.

export function riskBand(score: number | null | undefined): "high" | "med" | "low" | "none" {
  if (score == null) return "none";
  if (score >= 70) return "high";
  if (score >= 40) return "med";
  return "low";
}

export function riskColor(score: number | null | undefined): string {
  const b = riskBand(score);
  return b === "high" ? "var(--risk-high)" : b === "med" ? "var(--risk-med)"
    : b === "low" ? "var(--risk-low)" : "var(--text-dim)";
}

export function usd(amount: number): string {
  return new Intl.NumberFormat("en-US", {
    style: "currency", currency: "USD", maximumFractionDigits: 0,
  }).format(amount);
}

export function titleCase(s: string): string {
  return s.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

// Capital metric -> display, honoring the unit. Dollar/ratio figures are estimates.
export function formatCapital(metric: { amount: number; unit: string }): string {
  if (metric.unit === "USD") return usd(metric.amount);
  if (metric.unit === "ratio") return `${metric.amount.toFixed(2)}×`;
  return String(Math.round(metric.amount));
}

export function shortDate(iso: string): string {
  return new Date(iso).toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

// Month + year, for credential expiries and promotion dates (year matters there).
export function monthYear(iso: string): string {
  const d = new Date(iso);
  return isNaN(d.getTime()) ? iso : d.toLocaleDateString("en-US", { month: "short", year: "numeric" });
}
