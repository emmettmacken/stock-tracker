import {
  BacktestData, QuoteData, SignalData,
  FactorScoreData, SentimentData, InsiderData, ShortInterestData,
  SizingResult, PortfolioBacktestResult,
  WatchlistTicker, SignalLogEntry, TradeOutcome, PaperPosition, PaperAccount,
  AnalyticsData, SnapshotData, DecisionTrail, PriceHistory, Briefing, SectorExposure,
  EquityHistory, CompanyInfo, PortfolioHistory, EntrySignals, EdgeStats,
} from "./types";

export const BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export async function fetchSignal(ticker: string): Promise<SignalData> {
  const res = await fetch(`${BASE}/api/signal/${ticker}`);
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail ?? `Failed to fetch signal for ${ticker}`);
  }
  return res.json();
}

export async function fetchQuote(ticker: string): Promise<QuoteData> {
  const res = await fetch(`${BASE}/api/quote/${ticker}`);
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail ?? `Failed to fetch quote for ${ticker}`);
  }
  return res.json();
}

export async function fetchBacktest(ticker: string): Promise<BacktestData> {
  const res = await fetch(`${BASE}/api/backtest/${ticker}`);
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail ?? `Failed to run backtest for ${ticker}`);
  }
  return res.json();
}

// ── V3 API ────────────────────────────────────────────────────────────────────

export async function fetchFactors(ticker: string): Promise<FactorScoreData> {
  const res = await fetch(`${BASE}/api/factors/${ticker}`);
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail ?? `Failed to fetch factors for ${ticker}`);
  }
  return res.json();
}

export async function fetchCompany(ticker: string): Promise<CompanyInfo> {
  const res = await fetch(`${BASE}/api/company/${ticker}`);
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail ?? `Failed to fetch company info for ${ticker}`);
  }
  return res.json();
}

export async function fetchSentiment(ticker: string): Promise<SentimentData> {
  const res = await fetch(`${BASE}/api/sentiment/${ticker}`);
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail ?? `Failed to fetch sentiment for ${ticker}`);
  }
  return res.json();
}

export async function fetchInsider(ticker: string): Promise<InsiderData> {
  const res = await fetch(`${BASE}/api/insider/${ticker}`);
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail ?? `Failed to fetch insider data for ${ticker}`);
  }
  return res.json();
}

export async function fetchShortInterest(ticker: string): Promise<ShortInterestData> {
  const res = await fetch(`${BASE}/api/shortinterest/${ticker}`);
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail ?? `Failed to fetch short interest for ${ticker}`);
  }
  return res.json();
}

export async function fetchPortfolioSizing(req: {
  capital: number;
  tickers: string[];
  signals: Record<string, { composite_score: number; confidence: number }>;
}): Promise<SizingResult> {
  const res = await fetch(`${BASE}/api/portfolio/sizing`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail ?? "Failed to compute portfolio sizing");
  }
  return res.json();
}

export async function fetchPortfolioBacktest(req: {
  tickers: string[];
  capital: number;
}): Promise<PortfolioBacktestResult> {
  const res = await fetch(`${BASE}/api/portfolio/backtest`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail ?? "Failed to run portfolio backtest");
  }
  return res.json();
}

// ── V4 API ────────────────────────────────────────────────────────────────────

export async function fetchWatchlistDB(): Promise<WatchlistTicker[]> {
  const res = await fetch(`${BASE}/api/watchlist`);
  if (!res.ok) throw new Error("Failed to fetch watchlist");
  return res.json();
}

export async function addTickerDB(ticker: string): Promise<void> {
  const res = await fetch(`${BASE}/api/watchlist/${ticker}`, { method: "POST" });
  if (!res.ok) throw new Error(`Failed to add ${ticker} to watchlist`);
}

// Cached display data for the whole watchlist — one fast read, no live computation.
export async function fetchWatchlistSnapshot(): Promise<SnapshotData[]> {
  const res = await fetch(`${BASE}/api/watchlist/snapshot`);
  if (!res.ok) throw new Error("Failed to fetch watchlist snapshot");
  const data = await res.json();
  return data.snapshots ?? [];
}

// Explicit live recompute of a single ticker; returns its refreshed snapshot.
export async function refreshTicker(ticker: string): Promise<SnapshotData> {
  const res = await fetch(`${BASE}/api/watchlist/${ticker}/refresh`, { method: "POST" });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail ?? `Failed to refresh ${ticker}`);
  }
  return res.json();
}

export async function removeTickerDB(ticker: string): Promise<void> {
  const res = await fetch(`${BASE}/api/watchlist/${ticker}`, { method: "DELETE" });
  if (!res.ok) throw new Error(`Failed to remove ${ticker} from watchlist`);
}

export async function fetchPaperAccount(): Promise<PaperAccount> {
  const res = await fetch(`${BASE}/api/paper/account`);
  if (!res.ok) throw new Error("Failed to fetch account");
  return res.json();
}

export async function fetchPaperPositions(): Promise<{ available: boolean; positions?: PaperPosition[]; error?: string }> {
  const res = await fetch(`${BASE}/api/paper/positions`);
  if (!res.ok) throw new Error("Failed to fetch positions");
  return res.json();
}

export async function fetchEquityHistory(days = 30): Promise<EquityHistory> {
  const res = await fetch(`${BASE}/api/paper/equity-history?days=${days}`);
  if (!res.ok) throw new Error("Failed to fetch equity history");
  return res.json();
}

// Equity curve for the /portfolio page. `period` ∈ {1W,1M,3M,6M,1Y,all} — YTD is
// handled client-side by requesting 1Y and slicing to the calendar year.
export async function fetchPortfolioHistory(period: string): Promise<PortfolioHistory> {
  const res = await fetch(`${BASE}/api/portfolio/history?period=${period}`);
  if (!res.ok) throw new Error("Failed to fetch portfolio history");
  return res.json();
}

// Current account equity + timestamp for the 1D equity curve's live tip. Uncached on the
// backend — fetched every ~10s during market hours to extend only the line's last point.
export async function fetchLiveEquity(): Promise<{ equity: number; timestamp: string }> {
  const res = await fetch(`${BASE}/api/portfolio/live-equity`);
  if (!res.ok) throw new Error("Failed to fetch live equity");
  return res.json();
}

// Entry data ({ticker: {entry_score, entry_date, entry_price}}) for open positions,
// joined from signal_log's most recent BUY per still-open ticker.
export async function fetchEntrySignals(): Promise<EntrySignals> {
  const res = await fetch(`${BASE}/api/portfolio/positions/entry-signals`);
  if (!res.ok) throw new Error("Failed to fetch entry signals");
  return res.json();
}

// Close (all or part of) an open position via a market sell order. `qty` is in shares.
export async function closePosition(
  ticker: string,
  qty: number,
): Promise<{ success: boolean; order_id?: string; error?: string }> {
  const res = await fetch(`${BASE}/api/portfolio/positions/close`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ticker, qty }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail ?? `Failed to close position for ${ticker}`);
  }
  return res.json();
}

// Aggregate expectancy across all closed trades, for the Portfolio Edge Statistics
// section and the Strategy Lab sample-size caveat (uses the `n` field).
export async function fetchEdgeStats(): Promise<EdgeStats> {
  const res = await fetch(`${BASE}/api/portfolio/edge-stats`);
  if (!res.ok) throw new Error("Failed to fetch edge stats");
  return res.json();
}

export async function fetchSectorExposure(): Promise<SectorExposure> {
  const res = await fetch(`${BASE}/api/paper/sector-exposure`);
  if (!res.ok) throw new Error("Failed to fetch sector exposure");
  return res.json();
}

export async function fetchTradeHistory(): Promise<TradeOutcome[]> {
  const res = await fetch(`${BASE}/api/paper/history`);
  if (!res.ok) throw new Error("Failed to fetch trade history");
  return res.json();
}

export async function fetchSignalLog(limit = 50): Promise<SignalLogEntry[]> {
  const res = await fetch(`${BASE}/api/signals/log?limit=${limit}`);
  if (!res.ok) throw new Error("Failed to fetch signal log");
  return res.json();
}

export async function triggerSignalJob(): Promise<{ status: string; message: string }> {
  const res = await fetch(`${BASE}/api/paper/run-now`, { method: "POST" });
  if (!res.ok) throw new Error("Failed to trigger signal job");
  return res.json();
}

export async function fetchAnalytics(): Promise<AnalyticsData> {
  const res = await fetch(`${BASE}/api/analytics`);
  if (!res.ok) throw new Error("Failed to fetch analytics");
  return res.json();
}

export async function fetchDecisionTrail(ticker: string): Promise<DecisionTrail> {
  const res = await fetch(`${BASE}/api/decision-trail/${ticker}`);
  if (!res.ok) throw new Error(`Failed to fetch decision trail for ${ticker}`);
  return res.json();
}

export async function fetchBriefing(): Promise<Briefing> {
  const res = await fetch(`${BASE}/api/briefing`);
  if (!res.ok) throw new Error("Failed to fetch briefing");
  return res.json();
}

// Pass `period` (the chart selector: "1D".."Max") to have the backend scope the window and
// resolution server-side (intraday 1m/15m bars for 1D/1W, daily beyond, full history for Max).
// Pass { max: true } as a shorthand for the full-history fetch, or `days` for a plain daily
// window (used by the analytics buy & hold, which needs the full 760-bar daily series).
export async function fetchPriceHistory(
  ticker: string,
  opts: { days?: number; max?: boolean; period?: string } = {},
): Promise<PriceHistory> {
  const { days = 760, max = false, period } = opts;
  const query = max
    ? "period=max"
    : period
    ? `period=${encodeURIComponent(period)}`
    : `days=${days}`;
  const res = await fetch(`${BASE}/api/price-history/${ticker}?${query}`);
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail ?? `Failed to fetch price history for ${ticker}`);
  }
  return res.json();
}
