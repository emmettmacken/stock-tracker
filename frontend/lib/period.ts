// Shared time-period selector used across the stock detail page (price chart +
// per-ticker analytics). Picking a period scopes the whole page to that window.

export const PERIODS = ["1D", "1W", "1M", "3M", "YTD", "1Y", "Max"] as const;
export type Period = (typeof PERIODS)[number];

export const DEFAULT_PERIOD: Period = "3M";

// Human label for inline copy, e.g. "3M: +8.2%".
export const PERIOD_LABEL: Record<Period, string> = {
  "1D": "1D",
  "1W": "1W",
  "1M": "1M",
  "3M": "3M",
  YTD: "YTD",
  "1Y": "1Y",
  Max: "Max",
};

// Cutoff date (YYYY-MM-DD) for a period, anchored to the most recent data point
// so the window lines up with the last available trading day rather than "now".
// Returns null for "Max" (no lower bound — include everything).
export function periodCutoff(period: Period, lastDate: string): string | null {
  if (period === "Max") return null;
  const last = new Date(lastDate);
  if (period === "YTD") {
    return `${last.getUTCFullYear()}-01-01`;
  }
  const days: Record<Exclude<Period, "Max" | "YTD">, number> = {
    "1D": 1,
    "1W": 7,
    "1M": 30,
    "3M": 90,
    "1Y": 365,
  };
  const cutoff = new Date(last);
  cutoff.setUTCDate(cutoff.getUTCDate() - days[period as Exclude<Period, "Max" | "YTD">]);
  return cutoff.toISOString().slice(0, 10);
}
