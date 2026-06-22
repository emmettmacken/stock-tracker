export type Signal = "BUY" | "SELL" | "HOLD";
export type Regime = "bull" | "bear";

export interface QuoteData {
  ticker: string;
  price: number;
  prev_close: number;
  change_pct: number;
}

export interface SignalData extends QuoteData {
  signal: Signal;
  confidence: number;
  current_state: string;
  current_return_bucket: number;
  current_vol_bucket: number;
  bullish_edge: number;
  bearish_edge: number;
  bull_edge_ci_low: number;
  bull_edge_ci_high: number;
  bear_edge_ci_low: number;
  bear_edge_ci_high: number;
  n_obs_current_state: number;
  regime: Regime;
  high_confidence: boolean;
  transition_matrix_5x5: number[][];
  bullish_heatmap: number[][];   // [5][3]
  row_observations: number[][];  // [5][3]
  stationary_distribution: number[];
  return_labels: string[];
  vol_labels: string[];
  num_returns: number;
  regime_window_size: number;
}

export interface EquityPoint {
  date: string;
  strategy: number;
  bah: number;
}

export interface GateRejection {
  date: string;
  ticker: string;
  gate: string;
  score: number;
  detail: string;
}

export interface BacktestData {
  ticker: string;
  equity_curve: EquityPoint[];
  total_strategy_return: number;
  total_bah_return: number;
  sharpe_ratio: number;
  max_drawdown: number;
  win_rate_trades: number;
  num_trades: number;
  num_windows: number;
  gate_rejections?: GateRejection[];
  gate_rejection_summary?: Record<string, number>;
}

// ── V3 Types ──────────────────────────────────────────────────────────────────

export interface FactorDetail {
  score: number | null;
  weight: number;
  null: boolean;
}

export interface VolTrendDetail {
  price: number;
  ma20: number;
  ma50: number;
  ma200: number;
}

export interface EarningsDetail {
  surprises: number[]; // last two quarters, oldest → newest, as fractions (0.05 = +5%)
}

export interface FactorScoreData {
  ticker: string;
  factors: {
    hmm: FactorDetail;
    momentum: FactorDetail;
    vol_trend: FactorDetail;
    earnings: FactorDetail;
    insider: FactorDetail;
    sentiment?: FactorDetail;
  };
  composite_score: number;
  hmm_signal: string;
  hmm_confidence: number;
  hmm_regime?: "bull" | "bear" | "transition";
  min_factor_score: number | null;
  volume_ok: boolean;
  ret_3m: number | null;
  ret_12m: number | null;
  // Display-only raw breakdowns added by the backend for the stock detail page.
  // Optional: older cached snapshots won't have them.
  vol_trend_detail?: VolTrendDetail | null;
  earnings_detail?: EarningsDetail | null;
  // Display-only cached sector tag (for the watchlist sector filter). Not used in scoring.
  sector?: string | null;
  // Display-only company long name (e.g. "Apple Inc."). Null until first compute lands.
  company_name?: string | null;
}

// Cached company profile served by /api/company/{ticker}. All fields nullable —
// yfinance doesn't populate everything for every ticker.
export interface EarningsQuarter {
  date: string;
  eps_actual: number | null;
  eps_estimate: number | null;
  surprise_pct: number | null; // as a percentage, e.g. 4.2 means +4.2%
}

export interface CompanyInfo {
  ticker: string;
  name: string | null;
  sector: string | null;
  industry: string | null;
  summary: string | null;
  market_cap: number | null;
  trailing_pe: number | null;
  forward_pe: number | null;
  dividend_yield: number | null;
  fifty_two_week_high: number | null;
  fifty_two_week_low: number | null;
  // Financials panel — all optional/nullable; yfinance can omit any of them per ticker,
  // and older cached snapshots predate these fields entirely.
  peg_ratio?: number | null;
  price_to_sales?: number | null;
  price_to_book?: number | null;
  ev_to_ebitda?: number | null;
  profit_margin?: number | null;
  operating_margin?: number | null;
  return_on_equity?: number | null;
  revenue_growth?: number | null;
  debt_to_equity?: number | null;
  current_ratio?: number | null;
  free_cash_flow?: number | null;
  beta?: number | null;
  average_volume?: number | null;
  payout_ratio?: number | null;
  earnings: EarningsQuarter[];
}

// Cached display snapshot served by /api/watchlist/snapshot — no live computation.
// `factors` is null until the first compute lands (new ticker → "Calculating…").
export interface SnapshotData {
  ticker: string;
  added_at?: string | null;
  composite_score: number | null;
  signal: Signal | null;
  hmm_regime: "bull" | "bear" | "transition" | null;
  price: number | null;
  price_change_pct: number | null;
  computed_at: string | null;
  factors: FactorScoreData | null;
}

export interface EquityHistoryPoint {
  date: string;
  equity: number;
}

export interface EquityHistory {
  available: boolean;
  source?: "alpaca" | "reconstructed";
  approximate?: boolean;
  points?: EquityHistoryPoint[];
  error?: string;
}

// ── Portfolio page (/portfolio) ─────────────────────────────────────────────────

export interface PortfolioHistoryPoint {
  timestamp: string; // ISO string
  equity: number;
}

export interface PortfolioHistory {
  available: boolean;
  period?: string;
  points?: PortfolioHistoryPoint[];
  error?: string;
}

export interface EntrySignal {
  entry_score: number | null;
  entry_date: string | null;
  entry_price: number | null;
}

export interface EntrySignals {
  available: boolean;
  entries?: Record<string, EntrySignal>;
  error?: string;
}

export interface SectorBucket {
  sector: string;
  count: number;
  tickers: string[];
  pct: number;
  at_cap: boolean;
  near_cap: boolean;
}

export interface SectorExposure {
  available: boolean;
  error?: string;
  max_per_sector: number;
  total_positions: number;
  sectors: SectorBucket[];
}

export interface BriefingOrder {
  ticker: string;
  price: number | null;
  score: number | null;
  sizing_method: string | null;
}

export interface BriefingSkip {
  key: string;
  label: string;
  count: number;
}

export interface BriefingNearMiss {
  ticker: string;
  score: number;
  threshold: number;
  gap: number;
}

export interface BriefingAccount {
  available: boolean;
  equity?: number;
  equity_change_pct?: number;
  error?: string;
}

export interface Briefing {
  available: boolean;
  run_at: string | null;
  evaluated_count: number;
  evaluated_tickers?: string[];
  orders: BriefingOrder[];
  skip_breakdown: BriefingSkip[];
  near_misses: BriefingNearMiss[];
  macro_flags: string[];
  positions_closed: number;
  account: BriefingAccount;
}

export interface PricePoint {
  date: string;
  close: number;
  volume: number;
}

export interface PriceHistory {
  ticker: string;
  days?: number;        // present for windowed fetches
  period?: string;      // the selector period ("1D"…"Max") for period-scoped fetches
  intraday?: boolean;   // true for 1D/1W — `date` carries a full ISO timestamp, not YYYY-MM-DD
  visible_from?: string; // daily periods only: ISO date where the chart should start drawing
                         // (points before it are MA lead-in and must be trimmed from the axis)
  points: PricePoint[];
}

export interface DecisionGate {
  key: string;
  label: string;
  status: "passed" | "failed" | "ordered";
  detail: string;
}

export interface DecisionTrailOrder {
  price: number | null;
  kelly_fraction: number | null;
  sizing_method: string | null;
}

export interface DecisionTrail {
  ticker: string;
  evaluated: boolean;
  evaluated_at: string | null;
  outcome: "ordered" | "skipped" | "no_data" | "exit_only" | "other";
  would_trade_today: boolean;
  summary: string;
  gates: DecisionGate[];
  order: DecisionTrailOrder | null;
}

export type SentimentDirection = "bullish" | "neutral" | "bearish";

export interface SentimentData {
  available: boolean;
  reason?: string;
  ticker?: string;
  sentiment_score?: number;
  direction?: SentimentDirection;
  article_count?: number | null;
  buzz_score?: number | null;
  sector_vs_avg?: number | null;
  bearish_pct?: number;
  error?: string;
}

export interface InsiderData {
  available: boolean;
  ticker?: string;
  net_shares?: number;
  transaction_count?: number;
  direction?: "buying" | "selling" | "neutral";
  period_days?: number;
  error?: string;
}

export interface ShortInterestData {
  available: boolean;
  ticker?: string;
  short_float_pct?: number | null;
  short_ratio?: number | null;
  shares_short?: number | null;
  high_short_interest?: boolean;
  error?: string;
}

export interface TickerAllocation {
  kelly_fraction: number;
  kelly_dollar: number;
  vol_targeted_weight: number;
  vol_targeted_dollar: number;
  realised_vol_21d: number;
  correlation_penalty: number;
}

export interface SizingResult {
  capital: number;
  tickers: string[];
  allocations: Record<string, TickerAllocation>;
}

export interface EfficientFrontierPoint {
  return: number;
  volatility: number;
}

export interface RebalanceEvent {
  date: string;
  weights: Record<string, number>;
  signals: Record<string, string>;
}

export interface PortfolioEquityPoint {
  date: string;
  value: number;
  spy: number | null;
}

// ── V4 Types ──────────────────────────────────────────────────────────────────

export interface WatchlistTicker {
  ticker: string;
  added_at: string;
}

export interface SignalLogEntry {
  id: number;
  ticker: string;
  timestamp: string;
  composite_score: number | null;
  signal: string | null;
  action: string;
  skip_reason: string | null;
  price_at_signal: number | null;
  atr_at_signal: number | null;
}

export interface TradeOutcome {
  id: number;
  ticker: string;
  entry_signal_id: number | null;
  entry_price: number;
  exit_price: number;
  exit_reason: "sell_signal" | "stop_loss" | "max_hold_exit" | "score_deterioration" | "macro_drawdown_protection";
  return_pct: number;
  holding_days: number;
  composite_score_at_entry: number | null;
  exit_timestamp: string;
  entry_timestamp: string | null;
}

export interface PaperPosition {
  ticker: string;
  entry_price: number;
  current_price: number;
  pnl_pct: number;
  composite_score: number | null;
  atr_stop: number | null;
  trailing_stop: number | null;
  days_held: number;
  qty: number;
  market_value: number;
}

export interface PaperAccount {
  available: boolean;
  equity?: number;
  cash?: number;
  buying_power?: number;
  positions_count?: number;
  error?: string;
}

// Aggregate expectancy across all closed trades (Portfolio → Edge Statistics).
export interface EdgeStats {
  n: number;
  win_rate: number;        // percentage of trades with return_pct > 0
  avg_win_pct: number;     // average return_pct of winning trades
  avg_loss_pct: number;    // average return_pct of losing trades (≤ 0)
  expectancy_pct: number;  // expected return per trade, %
  avg_hold_days: number;
  low_sample?: boolean;    // true when n < 10
}

export interface PortfolioBacktestResult {
  tickers: string[];
  capital: number;
  equity_curve: PortfolioEquityPoint[];
  total_return_pct: number;
  spy_return_pct: number | null;
  sharpe_ratio: number;
  max_drawdown_pct: number;
  per_ticker_contrib: Record<string, number>;
  rebalance_events: RebalanceEvent[];
  efficient_frontier: EfficientFrontierPoint[];
}

// ── Analytics Types ───────────────────────────────────────────────────────────

export interface AnalyticsExitReason {
  exit_reason: string;
  avg_return: number;
  count: number;
}

export interface AnalyticsScoreBucket {
  bucket: string;
  avg_return: number;
  win_rate: number;
  count: number;
}

export interface AnalyticsTickerPerf {
  ticker: string;
  total_trades: number;
  win_rate: number;
  avg_return: number;
}

export interface AnalyticsData {
  by_exit_reason: AnalyticsExitReason[];
  by_score_bucket: AnalyticsScoreBucket[];
  by_ticker: AnalyticsTickerPerf[];
  adaptive_thresholds: {
    bull: number;
    bear: number;
    last_updated: string | null;
  };
  system_health: {
    last_signal_job: string | null;
    last_stoploss_job: string | null;
    open_positions: number;
    total_closed_trades: number;
  };
}
