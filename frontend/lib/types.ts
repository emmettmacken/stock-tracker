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

export interface BacktestData {
  ticker: string;
  equity_curve: EquityPoint[];
  total_strategy_return: number;
  total_bah_return: number;
  sharpe_ratio: number;
  max_drawdown: number;
  win_rate: number;
  num_trades: number;
  num_windows: number;
}

// ── V3 Types ──────────────────────────────────────────────────────────────────

export interface FactorDetail {
  score: number | null;
  weight: number;
  null: boolean;
}

export interface FactorScoreData {
  ticker: string;
  factors: {
    hmm: FactorDetail;
    momentum: FactorDetail;
    vol_trend: FactorDetail;
    earnings: FactorDetail;
    sentiment: FactorDetail;
    insider: FactorDetail;
  };
  composite_score: number;
  hmm_signal: string;
  hmm_confidence: number;
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
  exit_reason: "sell_signal" | "stop_loss" | "max_hold_exit";
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
