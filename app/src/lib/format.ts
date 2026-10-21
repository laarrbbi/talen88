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

// "$1.2M" / "$340k" / "$900" — for dense table cells.
export function usdCompact(amount: number): string {
  if (amount >= 1_000_000) return `$${(amount / 1_000_000).toFixed(1)}M`;
  if (amount >= 1_000) return `$${Math.round(amount / 1_000)}k`;
  return `$${Math.round(amount)}`;
}

export function ordinal(n: number): string {
  const s = n % 100 >= 11 && n % 100 <= 13 ? "th" : ({ 1: "st", 2: "nd", 3: "rd" } as Record<number, string>)[n % 10] ?? "th";
  return `${n}${s}`;
}

// Display names for capital metrics whose key reads poorly when title-cased.
const CAPITAL_LABELS: Record<string, string> = {
  expected_loss: "Dollars at risk (12 months)",
  cost_to_lose: "Cost to lose",
  suggested_retention_investment: "Suggested retention investment",
  retention_roi: "Retention ROI",
};

// Most decision-relevant figure first.
const CAPITAL_ORDER = ["expected_loss", "cost_to_lose", "suggested_retention_investment",
  "retention_roi", "compensation_efficiency", "value_score"];

export function byCapitalOrder(a: { metric: string }, b: { metric: string }): number {
  const ia = CAPITAL_ORDER.indexOf(a.metric), ib = CAPITAL_ORDER.indexOf(b.metric);
  return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib);
}

export function capitalLabel(metric: string): string {
  return CAPITAL_LABELS[metric] ?? titleCase(metric);
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
