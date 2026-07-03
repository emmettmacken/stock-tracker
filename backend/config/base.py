"""Versioned strategy configuration — the single source of truth for every tunable
strategy constant in the system.

Config consolidation (Jul 2026): before this module, strategy constants were either
hardcoded inline at each call site (often duplicated across the live trader and the two
backtests — the ATR_STOP_MULT-in-7-places problem) or read from the ``system_config`` DB
table with the *default* still hardcoded at every ``get_config`` call. This module holds
each default exactly ONCE.

Resolution model (see ``config/__init__.py``):
  * DB-overridable keys (marked "DB:" below) are still resolved through
    ``database.get_config`` — the DB value wins at runtime (so the adaptive-threshold job's
    writes to bull/bear_threshold still take effect), and the field here supplies the
    *default* only. This keeps live tuning and the pinned backtest baseline bit-identical.
  * Every other field is a pure-Python constant read directly off the config object.

Live/backtest split:
  * ``config/live.py``     exposes PRODUCTION_CONFIG = StrategyConfig() — the baseline the
    live trader imports.
  * ``config/backtest.py`` exposes BACKTEST_CONFIG = replace(PRODUCTION_CONFIG, ...) so a
    backtest experiment can override specific fields WITHOUT touching production. Overrides
    on DB-backed keys are honored only when the key isn't pinned in system_config, and
    ``resolve`` logs a warning when a pinned DB value shadows a backtest override.

REFACTOR INVARIANT: every value below equals the literal it replaced. Values moved; no
value changed. Verified against the pinned portfolio-backtest baseline (as_of 2026-06-30,
28 tickers: +7.52% return, Sharpe 0.933, max DD -4.79%, stops 46, profit-takes 9).
"""

from __future__ import annotations

from dataclasses import dataclass, field


# Change 2 (May 2026): sentiment added at 12%; earnings reduced 25->18%, insider 10->5%.
# Jun 2026: discrete-Markov factor removed; the five survivors kept their relative
# proportions and were renormalised (each / the pre-removal non-Markov sum 0.9296) so the
# weights again sum to exactly 1.0 with the same momentum/trend tilt as before.
DEFAULT_FACTOR_WEIGHTS: dict[str, float] = {
    "momentum":  0.33132530,
    "vol_trend": 0.26506024,
    "earnings":  0.20826162,
    "insider":   0.06626506,
    "sentiment": 0.12908778,
}


@dataclass(frozen=True)
class Thresholds:
    bull: float = 63.0             # DB: bull_threshold (adaptive-threshold job writes this)
    bear: float = 80.0             # DB: bear_threshold (adaptive-threshold job writes this)
    overextended_pct: float = 0.25  # DB: OVEREXTENDED_THRESHOLD_PCT
    volume_ratio: float = 1.05      # DB: VOLUME_THRESHOLD
    # Display-only BUY/HOLD/SELL badge bands (_composite_signal); the live trader gates on
    # the macro-aware bull/bear threshold above, never these.
    display_buy: float = 63.0
    display_sell: float = 45.0


@dataclass(frozen=True)
class Sizing:
    vol_contribution_target: float = 0.025  # DB: VOL_CONTRIBUTION_TARGET
    position_cap_pct: float = 0.10          # per-position hard cap (fraction of equity)
    position_floor_pct: float = 0.005       # min per-position size (was POSITION_FLOOR_PCT)
    portfolio_kelly_cap: float = 1.0        # aggregate exposure cap (was PORTFOLIO_KELLY_CAP)
    no_vol_fallback_weight: float = 0.05    # weight when no vol data is available
    ann_vol_floor: float = 0.001            # denominator floor for vol-target base weight
    # Conviction multiplier: score pivot->1x, ramps at 1/slope, capped. (75->1x, 95->1.5x)
    conviction_pivot: float = 75.0
    conviction_slope: float = 40.0
    conviction_cap: float = 1.5
    default_score: float = 75.0             # _position_dollars default score arg
    # Kelly
    kelly_fraction_mult: float = 0.5        # half-Kelly
    kelly_cap: float = 0.25
    kelly_edge_scale: float = 0.3
    kelly_variance_floor: float = 0.01
    kelly_ticker_min_trades: int = 10
    kelly_portfolio_min_trades: int = 20
    kelly_vol_target_cap_mult: float = 3.0  # Kelly capped at Nx the vol-target size
    # Performance multiplier (win-rate tilt on ticker history)
    perf_min_trades: int = 3
    perf_winrate_high: float = 0.6
    perf_winrate_low: float = 0.4
    perf_mult_high: float = 1.2
    perf_mult_low: float = 0.7
    # Correlation penalty: for the strongest correlation c > threshold,
    # penalty = 1 - (c - threshold)/corr_span * max_penalty. corr_span is stored as the
    # exact literal 0.3 (== 1 - threshold conceptually) rather than derived, to preserve
    # bit-for-bit floating-point identity (1.0 - 0.7 != 0.3 in IEEE754).
    corr_threshold: float = 0.7
    corr_span: float = 0.3
    corr_max_penalty: float = 0.5
    corr_window: int = 61                   # closes required (60 daily returns)
    corr_min_overlap: int = 10              # min overlapping return days
    # Priors used by /api/portfolio/sizing and the portfolio-backtest rebalance sizing.
    vol_prior_default: float = 0.25         # annualised vol prior when data is missing
    inv_vol_floor: float = 0.01             # denominator floor for inverse-vol weighting


@dataclass(frozen=True)
class Gates:
    hmm_bull_prob_min: float = 0.70         # live: smoothed HMM bull-prob entry gate
    hmm_composite_proxy: float = 70.0       # backtests: composite proxy for the above
    deterioration_exit: float = 40.0        # composite below this exits a held position
    vix_max: float = 30.0                   # VIX above this blocks new entries
    sentiment_min: float = 35.0             # negative-sentiment hard filter
    earnings_within_days: int = 2           # skip entries with earnings within N calendar days
    momentum_min_leg: float = -0.10         # 3m/12m disagreement leg floor
    reentry_cooldown_days: int = 2          # trading days after a non-signal exit
    factor_coverage_min: int = 3            # of 5 factors required before a deterioration SELL
    min_factor_floor_penalty: float = 5.0   # MIN_FACTOR_FLOOR caps score to buy_threshold - this
    max_sector_positions: int = 3           # DB: MAX_SECTOR_POSITIONS
    macro_drawdown_pct: float = -3.0        # SPY 5-day return below this closes all positions
    profit_take_trigger: float = 0.15       # +15% above entry trims half
    profit_take_fraction: float = 0.50      # fraction closed at the profit-take
    # DB: BACKTEST_HOLD (shared live max-hold + both backtests; key name is a known
    # pre-existing misnomer flagged for a separate future rename+migration).
    max_hold_days: int = 21


@dataclass(frozen=True)
class HMM:
    n_components: int = 2
    n_iter: int = 300
    random_state: int = 42
    kalman_q: float = 0.01
    kalman_r: float = 0.1
    normalize_window: int = 63
    regime_bull_prob: float = 0.65          # smoothed bull-prob above this -> "bull"
    regime_bear_prob: float = 0.35          # smoothed bull-prob below this -> "bear"


@dataclass(frozen=True)
class Factors:
    weights: dict[str, float] = field(default_factory=lambda: DEFAULT_FACTOR_WEIGHTS)
    weight_drift_log_threshold: float = 0.05
    # Momentum (3m + 12m, skip most recent 21 days, each horizon z-scored, clipped)
    mom_short_horizon: int = 63
    mom_long_horizon: int = 252
    mom_skip_days: int = 21
    mom_zclip: float = 3.0
    mom_quantile: float = 0.75              # top-quartile momentum override
    mom_quantile_window: int = 252
    # Vol-adjusted trend
    vt_ma_short: int = 20
    vt_ma_mid: int = 50
    vt_ma_long: int = 200
    vt_inv_vol_offset: float = 0.05
    vt_inv_vol_norm: float = 5.0
    vt_price_trend_band: float = 0.02
    vt_vol_ratio_low: float = 0.8
    vt_vol_ratio_high: float = 1.2
    vt_modifier_penalty: int = -8
    vt_modifier_bonus: int = 5
    vt_vol_ma_window: int = 20              # volume-divergence baseline MA window
    vt_vol_recent_window: int = 5           # volume-divergence recent-average window
    # Earnings
    earn_staleness_days: int = 120
    earn_band_high: float = 70.0
    earn_band_low: float = 30.0
    earn_neutral: float = 50.0
    earn_surprise_scale: float = 200.0
    # Insider (role-weighted Form 4 net shares)
    insider_role_officer: float = 1.0
    insider_role_director: float = 0.7
    insider_role_tenpct: float = 0.5
    insider_band_high: float = 70.0
    insider_band_low: float = 30.0
    insider_neutral: float = 50.0
    # ATR
    atr_period: int = 21
    atr_stop_mult: float = 2.5              # DB: ATR_STOP_MULT
    atr_fallback_pct: float = 0.02          # fallback ATR = this * price when ATR is NaN/<=0


@dataclass(frozen=True)
class Adaptive:
    bull_min: float = 63.0
    bull_max: float = 80.0
    bear_min: float = 75.0
    bear_max: float = 85.0
    ewa_alpha: float = 0.15                 # new = old*(1-alpha) + target*alpha
    min_trades: int = 5
    lookback_trades: int = 50
    winrate_clip_low: float = 0.4
    winrate_clip_high: float = 0.6
    # Stored as the exact literal 0.2 (== clip_high - clip_low) to preserve bit-for-bit FP
    # identity (0.6 - 0.4 != 0.2 in IEEE754).
    winrate_span: float = 0.2


@dataclass(frozen=True)
class Backtest:
    """Backtest-only knobs — deliberately NOT shared with the live trader."""
    train: int = 252                        # DB: BACKTEST_TRAIN
    test: int = 21                          # DB: BACKTEST_TEST
    tc_per_side: float = 0.0017             # 7bps commission + 0.1% slippage per side
    use_atr_stop: bool = True               # DB: BACKTEST_ATR_STOP
    macro_filter: bool = True               # DB: BACKTEST_MACRO_FILTER
    monte_carlo_runs: int = 500
    monte_carlo_seed: int = 42
    rejection_score_floor: float = 40.0     # diagnostic: only log rejections at/above this


@dataclass(frozen=True)
class StrategyConfig:
    thresholds: Thresholds = field(default_factory=Thresholds)
    sizing: Sizing = field(default_factory=Sizing)
    gates: Gates = field(default_factory=Gates)
    hmm: HMM = field(default_factory=HMM)
    factors: Factors = field(default_factory=Factors)
    adaptive: Adaptive = field(default_factory=Adaptive)
    backtest: Backtest = field(default_factory=Backtest)
