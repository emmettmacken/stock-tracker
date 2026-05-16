const KEY = "stock-tracker-watchlist";
const DEFAULTS = ["AAPL", "MSFT", "GOOGL"];

export function loadWatchlist(): string[] {
  if (typeof window === "undefined") return DEFAULTS;
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return DEFAULTS;
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) && parsed.length > 0 ? parsed : DEFAULTS;
  } catch {
    return DEFAULTS;
  }
}

export function saveWatchlist(tickers: string[]): void {
  if (typeof window === "undefined") return;
  localStorage.setItem(KEY, JSON.stringify(tickers));
}
