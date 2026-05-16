"""Stock Signal Tracker v2 — 2D Markov chain, HMM regimes, CI signals, walk-forward backtest."""
from __future__ import annotations
import warnings
import time
import logging
import os
import threading
from datetime import datetime, timedelta
from typing import Any, Optional
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import yfinance as yf
from yfinance.exceptions import YFTzMissingError, YFRateLimitError, YFTickerMissingError
from statsmodels.stats.proportion import proportion_confint
from hmmlearn import hmm
import httpx
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

try:
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger
    _scheduler = BackgroundScheduler(timezone=ZoneInfo("America/New_York"))
    SCHEDULER_OK = True
except Exception:
    _scheduler = None  # type: ignore[assignment]
    SCHEDULER_OK = False

try:
    from alpaca.trading.client import TradingClient
    from alpaca.trading.requests import MarketOrderRequest, GetOrdersRequest
    from alpaca.trading.enums import OrderSide, TimeInForce, QueryOrderStatus
    ALPACA_OK = True
except Exception:
    TradingClient = None  # type: ignore[assignment]
    ALPACA_OK = False

import database as db

warnings.filterwarnings("ignore", category=DeprecationWarning)
warnings.filterwarnings("ignore", category=UserWarning)
warnings.filterwarnings("ignore", message=".*convergence.*")
warnings.filterwarnings("ignore", message=".*not converging.*")
warnings.filterwarnings("ignore", message=".*Model is not.*")

logger = logging.getLogger("uvicorn.error")

app = FastAPI(title="Stock Signal Tracker v2")

_cors_origins_env = os.getenv("CORS_ORIGINS", "")
_extra_origins = [o.strip() for o in _cors_origins_env.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_extra_origins,
    allow_origin_regex=r"http://localhost:\d+",
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _startup() -> None:
    db.init_db()
    if SCHEDULER_OK and _scheduler is not None:
        ET = ZoneInfo("America/New_York")
        _scheduler.add_job(
            _run_signal_job,
            CronTrigger(day_of_week="mon-fri", hour=15, minute=45, timezone=ET),
            id="signal_job", replace_existing=True,
        )
        _scheduler.add_job(
            _run_stoploss_job,
            CronTrigger(day_of_week="mon-fri", hour=9, minute=35, timezone=ET),
            id="stoploss_job", replace_existing=True,
        )
        _scheduler.start()
        logger.info("Scheduler started (signal@15:45 ET, stop-loss@09:35 ET)")
    else:
        logger.warning("APScheduler not available — scheduled jobs disabled")


@app.on_event("shutdown")
def _shutdown() -> None:
    if SCHEDULER_OK and _scheduler is not None and _scheduler.running:
        _scheduler.shutdown(wait=False)

# ── Constants ─────────────────────────────────────────────────────────────────

RETURN_LABELS = ["Strong Down", "Down", "Flat", "Up", "Strong Up"]
VOL_LABELS    = ["Low Vol", "Mid Vol", "High Vol"]
N_RET    = 5
N_VOL    = 3
N_ST     = N_RET * N_VOL   # 15

RET_THRESHOLDS = (-0.015, -0.003, 0.003, 0.015)
MIN_OBS        = 15
ROLLING_WINDOW = 252
_SENTIMENT_CACHE: dict[str, tuple[dict, float]] = {}  # ticker → (result, timestamp)
_SENTIMENT_TTL = 900  # 15 minutes
_SENTIMENT_LOCK = threading.Lock()
_SENTIMENT_LAST_CALL = 0.0
_SENTIMENT_MIN_INTERVAL = 13.0  # ~4.5 req/min, safely under the 5/min AV free-tier limit
_SECTOR_CACHE: dict[str, tuple[str, float]] = {}  # ticker → (sector, timestamp)
_SECTOR_TTL = 86400  # 24 hours

# ── State helpers ─────────────────────────────────────────────────────────────

def si(ret: int, vol: int) -> int:
    return ret * N_VOL + vol

def decode(s: int) -> tuple[int, int]:
    return s // N_VOL, s % N_VOL

def ret_bucket(r: float) -> int:
    if r < RET_THRESHOLDS[0]: return 0
    if r < RET_THRESHOLDS[1]: return 1
    if r < RET_THRESHOLDS[2]: return 2
    if r < RET_THRESHOLDS[3]: return 3
    return 4

def vol_bucket(ratio: float, lo: float, hi: float) -> int:
    if ratio < lo: return 0
    if ratio > hi: return 2
    return 1

BULL_STATES = [s for s in range(N_ST) if decode(s)[0] in (3, 4)]
BEAR_STATES = [s for s in range(N_ST) if decode(s)[0] in (0, 1)]

# ── Feature preparation ───────────────────────────────────────────────────────

def extract_features(df: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Returns (closes, returns, vol_aligned, vol_20d_aligned) — all len(df)-1 except closes."""
    closes  = df["Close"].values.astype(float)
    volumes = df["Volume"].values.astype(float)
    returns = np.diff(closes) / closes[:-1]
    vol_20d = pd.Series(volumes).rolling(20, min_periods=1).mean().values
    # returns[i] corresponds to close[i+1]; align volume/20d to that day
    return closes, returns, volumes[1:], vol_20d[1:]

def make_state_seq(
    returns: np.ndarray,
    vol: np.ndarray,
    vol_20d: np.ndarray,
) -> tuple[list[int], float, float]:
    """Build 2D state list + vol percentile thresholds for this window."""
    ratios = vol / np.maximum(vol_20d, 1.0)
    lo_t = float(np.percentile(ratios, 33))
    hi_t = float(np.percentile(ratios, 67))
    states = [si(ret_bucket(r), vol_bucket(vr, lo_t, hi_t)) for r, vr in zip(returns, ratios)]
    return states, lo_t, hi_t

# ── Transition matrix ─────────────────────────────────────────────────────────

def build_matrix(
    states: list[int],
    fallback: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Returns (normalized_matrix, row_obs_counts, raw_counts).
    Rows < MIN_OBS fall back to `fallback` (uniform if not given).
    """
    counts = np.zeros((N_ST, N_ST), dtype=float)
    for a, b in zip(states[:-1], states[1:]):
        counts[a, b] += 1
    row_obs = counts.sum(axis=1)
    fb = fallback if fallback is not None else np.full(N_ST, 1.0 / N_ST)
    mat = np.zeros((N_ST, N_ST), dtype=float)
    for i in range(N_ST):
        if row_obs[i] >= MIN_OBS:
            mat[i] = counts[i] / row_obs[i]
        else:
            mat[i] = fb
    return mat, row_obs, counts

def stationary(mat: np.ndarray, iters: int = 1000) -> np.ndarray:
    pi = np.full(mat.shape[0], 1.0 / mat.shape[0])
    for _ in range(iters):
        pi = pi @ mat
    return pi

# ── HMM regime detection ──────────────────────────────────────────────────────

def fit_regimes(returns: np.ndarray) -> tuple[np.ndarray, int, hmm.GaussianHMM]:
    """
    2-state Gaussian HMM. Returns (regime_seq, bull_id, model).
    Bull = state with the higher mean return.
    """
    X = returns.reshape(-1, 1)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        model = hmm.GaussianHMM(
            n_components=2, covariance_type="full",
            n_iter=300, random_state=42,
        )
        model.fit(X)
    seq = model.predict(X)
    bull_id = int(np.argmax([model.means_[0][0], model.means_[1][0]]))
    return seq.astype(int), bull_id, model

# ── Signal with CI ────────────────────────────────────────────────────────────

def compute_signal(
    counts_row: np.ndarray,
    trans_row: np.ndarray,
    stat: np.ndarray,
) -> dict:
    n_total = int(counts_row.sum())
    n_bull  = int(counts_row[BULL_STATES].sum())
    n_bear  = int(counts_row[BEAR_STATES].sum())
    s_bull  = float(stat[BULL_STATES].sum())
    s_bear  = float(stat[BEAR_STATES].sum())

    if n_total >= MIN_OBS:
        b_lo, b_hi = proportion_confint(n_bull, n_total, alpha=0.05, method="wilson")
        r_lo, r_hi = proportion_confint(n_bear, n_total, alpha=0.05, method="wilson")
    else:
        p = n_bull / max(n_total, 1)
        q = n_bear / max(n_total, 1)
        b_lo = b_hi = p
        r_lo = r_hi = q

    bull_edge = float(trans_row[BULL_STATES].sum()) - s_bull
    bear_edge = float(trans_row[BEAR_STATES].sum()) - s_bear
    belo = b_lo - s_bull
    behi = b_hi - s_bull
    relo = r_lo - s_bear
    rehi = r_hi - s_bear

    if bull_edge > 0 and belo > 0:
        signal = "BUY"
        confidence = min(belo / 0.15, 1.0)
    elif bear_edge > 0 and relo > 0:
        signal = "SELL"
        confidence = min(relo / 0.15, 1.0)
    else:
        signal = "HOLD"
        confidence = float(np.clip(1.0 - max(abs(belo), abs(relo)) / 0.1 * 0.5, 0.3, 1.0))

    return {
        "signal":            signal,
        "confidence":        round(confidence, 4),
        "bullish_edge":      round(bull_edge, 4),
        "bearish_edge":      round(bear_edge, 4),
        "bull_edge_ci_low":  round(belo, 4),
        "bull_edge_ci_high": round(behi, 4),
        "bear_edge_ci_low":  round(relo, 4),
        "bear_edge_ci_high": round(rehi, 4),
        "n_obs_current_state": n_total,
    }

# ── Matrix summarization helpers ──────────────────────────────────────────────

def marginal_5x5(mat: np.ndarray, row_obs: np.ndarray) -> list[list[float]]:
    """Observation-weighted 5×5 matrix (marginalized over vol buckets)."""
    m, w = np.zeros((N_RET, N_RET)), np.zeros(N_RET)
    for r1 in range(N_RET):
        for v1 in range(N_VOL):
            n = row_obs[si(r1, v1)]
            if n > 0:
                for r2 in range(N_RET):
                    m[r1, r2] += n * sum(mat[si(r1, v1), si(r2, v2)] for v2 in range(N_VOL))
                w[r1] += n
        if w[r1] > 0:
            m[r1] /= w[r1]
        else:
            m[r1] = np.full(N_RET, 1.0 / N_RET)
    return m.tolist()

def bullish_heatmap_5x3(mat: np.ndarray) -> list[list[float]]:
    """5×3 array: P(next return bullish | current (return_bucket, vol_bucket))."""
    return [
        [round(float(sum(mat[si(r, v), bs] for bs in BULL_STATES)), 4) for v in range(N_VOL)]
        for r in range(N_RET)
    ]

def obs_grid_5x3(row_obs: np.ndarray) -> list[list[int]]:
    return [[int(row_obs[si(r, v)]) for v in range(N_VOL)] for r in range(N_RET)]

# ── Data fetching ─────────────────────────────────────────────────────────────

_T_EXC = (YFTzMissingError, YFRateLimitError)
_T_STR = ("Expecting value", "JSONDecodeError", "ConnectionError",
          "RemoteDisconnected", "ChunkedEncodingError", "Read timed out")

def _transient(e: Exception) -> bool:
    return isinstance(e, _T_EXC) or any(p in str(e) for p in _T_STR)

def fetch_ohlcv(ticker: str, days: int = 760, min_bars: int = 50, max_retries: int = 3) -> pd.DataFrame:
    end, start = datetime.now(), datetime.now() - timedelta(days=days)
    last_exc = None
    for attempt in range(max_retries):
        try:
            df = yf.Ticker(ticker).history(
                start=start.strftime("%Y-%m-%d"), end=end.strftime("%Y-%m-%d"),
                auto_adjust=True, raise_errors=True,
            )
            if df.empty or len(df) < min_bars:
                raise HTTPException(status_code=404, detail=f"Insufficient data for '{ticker}'")
            return df[["Close", "Volume"]].dropna()
        except HTTPException:
            raise
        except Exception as exc:
            last_exc = exc
            if "Quote not found" in str(exc) or isinstance(exc, YFTickerMissingError):
                raise HTTPException(status_code=404, detail=f"'{ticker}' not found")
            if _transient(exc):
                wait = 1.5 * (attempt + 1)
                logger.warning("Transient %s attempt %d: %s (retry %.1fs)", ticker, attempt + 1, exc, wait)
                time.sleep(wait)
                continue
            if attempt < max_retries - 1:
                time.sleep(1.0)
                continue
            break
    if isinstance(last_exc, (YFTzMissingError, YFTickerMissingError)):
        raise HTTPException(status_code=404, detail=f"'{ticker}' not found after {max_retries} attempts")
    raise HTTPException(status_code=502, detail=f"Failed fetching '{ticker}': {last_exc}")

# ── /api/quote ────────────────────────────────────────────────────────────────

@app.get("/api/quote/{ticker}")
def get_quote(ticker: str):
    ticker = ticker.upper()
    df = fetch_ohlcv(ticker, days=10, min_bars=2)
    c = df["Close"].values
    cur, prev = float(c[-1]), float(c[-2])
    return {
        "ticker": ticker, "price": round(cur, 4), "prev_close": round(prev, 4),
        "change_pct": round((cur - prev) / prev * 100, 4),
    }

# ── /api/signal ───────────────────────────────────────────────────────────────

@app.get("/api/signal/{ticker}")
def get_signal(ticker: str):
    ticker = ticker.upper()

    # 2 years of OHLCV
    df = fetch_ohlcv(ticker, days=760, min_bars=260)
    closes, returns, vol, vol_20d = extract_features(df)

    # Fit HMM on full 2-year return series
    try:
        regime_seq, bull_id, _ = fit_regimes(returns)
    except Exception as e:
        logger.warning("HMM failed for %s: %s — using single regime", ticker, e)
        regime_seq = np.zeros(len(returns), dtype=int)
        bull_id = 0

    bear_id = 1 - bull_id

    # Rolling 252-day window for transition matrices
    w = min(ROLLING_WINDOW, len(returns))
    rets_w    = returns[-w:]
    vol_w     = vol[-w:]
    vol_20d_w = vol_20d[-w:]
    reg_w     = regime_seq[-w:]

    states_all, lo_t, hi_t = make_state_seq(rets_w, vol_w, vol_20d_w)

    # Full-window matrix for fallback stationary
    mat_all, row_obs_all, cnt_all = build_matrix(states_all)
    stat_all = stationary(mat_all)

    # Per-regime state lists
    bull_st = [s for s, r in zip(states_all, reg_w) if r == bull_id]
    bear_st = [s for s, r in zip(states_all, reg_w) if r == bear_id]

    # Active regime
    current_regime_id = int(regime_seq[-1])
    regime_label = "bull" if current_regime_id == bull_id else "bear"
    active_st = bull_st if current_regime_id == bull_id else bear_st

    if len(active_st) >= 20:
        mat_active, row_obs, cnt_active = build_matrix(active_st, fallback=stat_all)
    else:
        mat_active, row_obs, cnt_active = mat_all, row_obs_all, cnt_all
    stat_active = stationary(mat_active)

    # Current 2D state
    ratios_w = vol_w / np.maximum(vol_20d_w, 1.0)
    cur_rb = ret_bucket(float(rets_w[-1]))
    cur_vb = vol_bucket(float(ratios_w[-1]), lo_t, hi_t)
    cur_st = si(cur_rb, cur_vb)

    sig = compute_signal(cnt_active[cur_st], mat_active[cur_st], stat_active)

    # Price
    cur_price  = float(closes[-1])
    prev_price = float(closes[-2])

    return {
        "ticker":      ticker,
        "price":       round(cur_price, 4),
        "prev_close":  round(prev_price, 4),
        "change_pct":  round((cur_price - prev_price) / prev_price * 100, 4),
        **sig,
        "regime":                 regime_label,
        "current_state":          f"{RETURN_LABELS[cur_rb]}-{VOL_LABELS[cur_vb]}",
        "current_return_bucket":  cur_rb,
        "current_vol_bucket":     cur_vb,
        "transition_matrix_5x5":  marginal_5x5(mat_active, row_obs),
        "bullish_heatmap":        bullish_heatmap_5x3(mat_active),
        "row_observations":       obs_grid_5x3(row_obs),
        "stationary_distribution": [
            round(float(sum(stat_active[si(r, v)] for v in range(N_VOL))), 4)
            for r in range(N_RET)
        ],
        "return_labels":  RETURN_LABELS,
        "vol_labels":     VOL_LABELS,
        "high_confidence": bool(np.all(row_obs >= MIN_OBS)),
        "num_returns":     len(rets_w),
        "regime_window_size": len(active_st),
    }

# ── /api/backtest ─────────────────────────────────────────────────────────────

@app.get("/api/backtest/{ticker}")
def get_backtest(ticker: str):
    ticker = ticker.upper()

    df = fetch_ohlcv(ticker, days=760, min_bars=260)
    closes, returns, vol, vol_20d = extract_features(df)
    dates = df.index.tolist()[1:]
    n = len(returns)  # = len(closes) - 1

    TRAIN, TEST, HOLD = 252, 21, 5

    if n < TRAIN + TEST:
        raise HTTPException(status_code=400, detail="Need at least 2 years of history for backtest")

    # Walk-forward simulation
    portfolio   = 1.0
    bah_base    = float(closes[TRAIN])
    in_pos      = False
    hold_left   = 0
    daily_strat: list[float] = []
    equity_curve: list[dict] = []
    trades: list[float] = []

    test_start = TRAIN
    while test_start + TEST <= n:
        ts, te = test_start - TRAIN, test_start

        tr_rets, tr_vol, tr_20d = returns[ts:te], vol[ts:te], vol_20d[ts:te]
        tr_states, lo_t, hi_t = make_state_seq(tr_rets, tr_vol, tr_20d)

        mat_full, _, cnt_full = build_matrix(tr_states)
        stat_full = stationary(mat_full)

        # Fit HMM on training window only — no future data in model parameters
        try:
            tr_regime_seq, tr_bull_id, tr_model = fit_regimes(tr_rets)
            test_len = min(TEST, n - te)
            test_regime_seq = tr_model.predict(returns[te:te + test_len].reshape(-1, 1))
        except Exception:
            tr_regime_seq = np.zeros(len(tr_rets), dtype=int)
            test_regime_seq = np.zeros(min(TEST, n - te), dtype=int)
            tr_bull_id = 0

        bull_tr = [s for s, r in zip(tr_states, tr_regime_seq) if r == tr_bull_id]
        bear_tr = [s for s, r in zip(tr_states, tr_regime_seq) if r != tr_bull_id]

        mat_bull, _, cnt_bull = build_matrix(bull_tr if len(bull_tr) >= 20 else tr_states, fallback=stat_full)
        mat_bear, _, cnt_bear = build_matrix(bear_tr if len(bear_tr) >= 20 else tr_states, fallback=stat_full)
        stat_bull = stationary(mat_bull)
        stat_bear = stationary(mat_bear)

        for idx in range(test_start, min(test_start + TEST, n)):
            r_t    = returns[idx]
            ratio  = vol[idx] / max(vol_20d[idx], 1.0)
            cur_st = si(ret_bucket(r_t), vol_bucket(ratio, lo_t, hi_t))
            reg_t  = int(test_regime_seq[idx - test_start])

            if reg_t == tr_bull_id:
                mat_a, cnt_a, stat_a = mat_bull, cnt_bull, stat_bull
            else:
                mat_a, cnt_a, stat_a = mat_bear, cnt_bear, stat_bear

            signal = compute_signal(cnt_a[cur_st], mat_a[cur_st], stat_a)["signal"]

            # Portfolio update: earn r_t if already in position BEFORE this signal
            if in_pos:
                portfolio *= (1 + r_t)
                daily_strat.append(r_t)
                hold_left -= 1
                if signal == "SELL" or hold_left <= 0:
                    trades.append(portfolio - 1.0)  # rough trade profit marker
                    in_pos = False
            else:
                daily_strat.append(0.0)
                if signal == "BUY":
                    in_pos = True
                    hold_left = HOLD

            bah_val = float(closes[idx + 1]) / bah_base if idx + 1 < len(closes) else float(closes[-1]) / bah_base
            raw_date = dates[idx]
            date_str = raw_date.strftime("%Y-%m-%d") if hasattr(raw_date, "strftime") else str(raw_date)[:10]
            equity_curve.append({"date": date_str, "strategy": round(portfolio, 6), "bah": round(bah_val, 6)})

        test_start += TEST

    # Aggregate
    strat_pct = (portfolio - 1.0) * 100
    bah_pct   = (float(closes[-1]) / bah_base - 1.0) * 100

    daily_arr = np.array(daily_strat)
    sharpe = float(daily_arr.mean() / daily_arr.std() * np.sqrt(252)) if daily_arr.std() > 1e-10 else 0.0

    if len(equity_curve) > 0:
        cv = np.array([pt["strategy"] for pt in equity_curve])
        peaks = np.maximum.accumulate(cv)
        max_dd = float(((cv - peaks) / np.maximum(peaks, 1e-9)).min() * 100)
    else:
        max_dd = 0.0

    # Win rate: fraction of completed trades that ended profitably
    # Each trade in `trades` holds the cumulative portfolio value minus 1 at close.
    # A simpler proxy: fraction of "in-position" daily returns > 0
    pos_rets = [r for r in daily_strat if r != 0.0]
    win_rate = float(sum(r > 0 for r in pos_rets) / len(pos_rets) * 100) if pos_rets else 0.0

    return {
        "ticker":                  ticker,
        "equity_curve":            equity_curve,
        "total_strategy_return":   round(strat_pct, 2),
        "total_bah_return":        round(bah_pct, 2),
        "sharpe_ratio":            round(sharpe, 3),
        "max_drawdown":            round(max_dd, 2),
        "win_rate":                round(win_rate, 1),
        "num_trades":              len(trades),
        "num_windows":             (n - TRAIN) // TEST,
    }

# ── /health ───────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok", "time": datetime.now().isoformat()}


# ═══════════════════════════════════════════════════════════════════════════════
# FACTOR ENGINE
# ═══════════════════════════════════════════════════════════════════════════════

def _hmm_factor_score(signal: str, confidence: float) -> float:
    """Map HMM signal+confidence → 0–100."""
    if signal == "BUY":
        return 75.0 + confidence * 25.0
    if signal == "SELL":
        return 25.0 - confidence * 25.0
    return 40.0 + (confidence - 0.5) * 40.0  # HOLD: 40–60 range


def _momentum_score(closes: np.ndarray) -> float | None:
    """3m+12m momentum, skip last 21 days, z-score vs rolling 252d, clip±3, scale 0–100."""
    if len(closes) < 252 + 21 + 2:
        return None
    # Skip most recent 21 days
    c = closes[:-21]
    m3  = (c[-1] / c[-63]  - 1.0) if len(c) >= 63  else None
    m12 = (c[-1] / c[-252] - 1.0) if len(c) >= 252 else None
    if m3 is None and m12 is None:
        return None
    raw = np.mean([x for x in [m3, m12] if x is not None])
    # z-score against rolling 252-day distribution of the 63-day return
    if len(c) >= 252 + 63:
        window_rets = np.array([(c[i] / c[i - 63] - 1.0) for i in range(63, len(c))])
        mu, sigma = window_rets[-252:].mean(), window_rets[-252:].std()
        z = (raw - mu) / (sigma + 1e-10)
    else:
        z = raw / 0.1  # crude normalisation
    z = float(np.clip(z, -3, 3))
    return (z + 3) / 6 * 100


def _rsi14(closes: np.ndarray) -> float:
    """RSI(14) computed manually."""
    if len(closes) < 15:
        return 50.0
    diffs = np.diff(closes[-15:])
    gains = np.where(diffs > 0, diffs, 0.0)
    losses = np.where(diffs < 0, -diffs, 0.0)
    avg_gain = gains.mean()
    avg_loss = losses.mean()
    if avg_loss < 1e-10:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def _mean_reversion_score(closes: np.ndarray) -> float | None:
    if len(closes) < 252:
        return None
    rsi = _rsi14(closes)
    mu = closes[-252:].mean()
    sigma = closes[-252:].std()
    z_price = (closes[-1] - mu) / (sigma + 1e-10)
    # High RSI + extended above mean → overbought → LOW score
    rsi_inv = 100.0 - rsi  # inverted: high RSI becomes low score component
    z_score_inv = float(np.clip(-z_price, -3, 3))
    z_component = (z_score_inv + 3) / 6 * 100
    return 0.5 * rsi_inv + 0.5 * z_component


def _vol_trend_score(closes: np.ndarray) -> float | None:
    if len(closes) < 201:
        return None
    ma20  = closes[-20:].mean()
    ma50  = closes[-50:].mean()
    ma200 = closes[-200:].mean()
    price = closes[-1]
    alignment = sum([price > ma20, ma20 > ma50, ma50 > ma200])
    raw = alignment / 3.0  # 0, 1/3, 2/3, 1
    # Realised vol over last 21 days
    ret21 = np.diff(closes[-22:]) / closes[-22:-1]
    rvol = ret21.std() * np.sqrt(252)
    inv_vol_weight = 1.0 / (rvol + 0.05)  # cap weight for very low vol
    # Rescale: already 0-1, apply vol weight as a squeeze toward 50 for high-vol
    vol_factor = min(inv_vol_weight / 5.0, 1.0)  # normalise so typical ~0.5 maps to 1
    score = 50.0 + (raw - 0.5) * 100.0 * vol_factor
    return float(np.clip(score, 0, 100))


def _earnings_score(ticker_obj: yf.Ticker) -> float | None:
    try:
        eh = ticker_obj.earnings_history
        if eh is None or len(eh) < 2:
            return None
        # Most recent two quarters
        last2 = eh.sort_index().tail(2)
        surprises = []
        for col in ["epsActual", "epsEstimate"]:
            if col not in last2.columns:
                return None
        for _, row in last2.iterrows():
            est, act = row.get("epsEstimate"), row.get("epsActual")
            if pd.isna(est) or pd.isna(act) or est == 0:
                return None
            surprises.append((act - est) / abs(est))
        if len(surprises) < 2:
            return None
        pos = sum(s > 0 for s in surprises)
        if pos == 2:
            avg = np.mean(surprises)
            return float(np.clip(70.0 + avg * 200.0, 70.0, 100.0))
        if pos == 0:
            avg = np.mean(surprises)
            return float(np.clip(30.0 + avg * 200.0, 0.0, 30.0))
        return 50.0
    except Exception:
        return None


def _get_sentiment_score(ticker: str) -> float | None:
    """Return 0–100 sentiment score from cache or Alpha Vantage.
    No local rate-limiter here — the per-request guard lives in get_sentiment().
    Alpha Vantage returns a Note/Information key when over-limit; we handle it gracefully."""
    api_key = os.getenv("ALPHA_VANTAGE_KEY", "")
    if not api_key:
        return None
    cached, ts = _SENTIMENT_CACHE.get(ticker, ({}, 0.0))
    if cached and cached.get("available") and (time.time() - ts) < _SENTIMENT_TTL:
        return cached.get("sentiment_score")
    try:
        url = (
            f"https://www.alphavantage.co/query"
            f"?function=NEWS_SENTIMENT&tickers={ticker}&apikey={api_key}"
        )
        with httpx.Client(timeout=10) as client:
            resp = client.get(url)
        if resp.status_code != 200:
            return None
        data = resp.json()
        if "Note" in data or "Information" in data:
            return None
        feed = data.get("feed", [])
        scores = [float(a["overall_sentiment_score"]) for a in feed if "overall_sentiment_score" in a]
        if not scores:
            return None
        avg = sum(scores) / len(scores)
        score = round((avg + 1) / 2 * 100, 1)
        direction = "bullish" if score >= 60 else ("bearish" if score <= 40 else "neutral")
        _SENTIMENT_CACHE[ticker] = (
            {"available": True, "ticker": ticker, "sentiment_score": score, "direction": direction,
             "article_count": int(data.get("items", len(feed))), "buzz_score": None,
             "sector_vs_avg": None, "bearish_pct": None},
            time.time(),
        )
        return score
    except Exception:
        return None


def _get_insider_score(ticker: str) -> float | None:
    """Return 0–100 insider score from SEC Form 4: 70=net buying, 50=neutral, 30=net selling."""
    try:
        today = datetime.now().strftime("%Y-%m-%d")
        start = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
        url = (
            f"https://efts.sec.gov/LATEST/search-index?q=%22{ticker}%22"
            f"&dateRange=custom&startdt={start}&enddt={today}&forms=4"
        )
        headers = {"User-Agent": "stock-tracker emmettmacken@gmail.com"}
        with httpx.Client(timeout=10, follow_redirects=True) as client:
            resp = client.get(url, headers=headers)
        if resp.status_code != 200:
            return None
        hits = resp.json().get("hits", {}).get("hits", [])
        if not hits:
            return 50.0
        net = 0.0
        for hit in hits:
            src = hit.get("_source", {})
            tx = src.get("transaction_type", "")
            try:
                shares = float(src.get("shares", 0) or 0)
            except (ValueError, TypeError):
                shares = 0.0
            if tx == "P":
                net += shares
            elif tx == "S":
                net -= shares
        return 70.0 if net > 0 else (30.0 if net < 0 else 50.0)
    except Exception:
        return None


def _get_sector(ticker: str) -> str:
    """Return the sector string for a ticker, cached for 24 h."""
    cached_sector, ts = _SECTOR_CACHE.get(ticker, ("", 0.0))
    if cached_sector and (time.time() - ts) < _SECTOR_TTL:
        return cached_sector
    try:
        sector = yf.Ticker(ticker).info.get("sector", "Unknown") or "Unknown"
        _SECTOR_CACHE[ticker] = (sector, time.time())
        return sector
    except Exception:
        return "Unknown"


def _compute_factors(ticker: str) -> Optional[dict]:
    """Core factor computation shared by the API endpoint and the scheduler."""
    try:
        df = fetch_ohlcv(ticker, days=760, min_bars=260)
    except Exception:
        return None

    closes, returns, vol, vol_20d = extract_features(df)

    try:
        regime_seq, bull_id, _ = fit_regimes(returns)
    except Exception:
        regime_seq = np.zeros(len(returns), dtype=int)
        bull_id = 0

    w = min(ROLLING_WINDOW, len(returns))
    rets_w, vol_w, vol_20d_w, reg_w = returns[-w:], vol[-w:], vol_20d[-w:], regime_seq[-w:]
    states_all, lo_t, hi_t = make_state_seq(rets_w, vol_w, vol_20d_w)
    mat_all, _, cnt_all = build_matrix(states_all)
    stat_all = stationary(mat_all)

    active_id = int(regime_seq[-1])
    active_st = [s for s, r in zip(states_all, reg_w) if r == active_id]
    if len(active_st) >= 20:
        mat_act, _, cnt_act = build_matrix(active_st, fallback=stat_all)
    else:
        mat_act, cnt_act = mat_all, cnt_all
    stat_act = stationary(mat_act)

    ratios_w = vol_w / np.maximum(vol_20d_w, 1.0)
    cur_st = si(ret_bucket(float(rets_w[-1])), vol_bucket(float(ratios_w[-1]), lo_t, hi_t))
    sig = compute_signal(cnt_act[cur_st], mat_act[cur_st], stat_act)

    hmm_score     = _hmm_factor_score(sig["signal"], sig["confidence"])
    mom_score     = _momentum_score(closes)
    vt_score      = _vol_trend_score(closes)
    earn_score    = _earnings_score(yf.Ticker(ticker))
    sent_score    = _get_sentiment_score(ticker)
    insider_score = _get_insider_score(ticker)

    # Volume and overextension signals used by the signal job
    current_vol  = float(vol[-1]) if len(vol) > 0 else 0.0
    avg_vol_20d  = float(vol_20d[-1]) if len(vol_20d) > 0 else 1.0
    volume_ok    = current_vol > 1.2 * avg_vol_20d if avg_vol_20d > 0 else True
    ma20         = float(closes[-20:].mean()) if len(closes) >= 20 else float(closes[-1])
    overextended = float(closes[-1]) > ma20 * 1.15

    factors: dict[str, Any] = {
        "hmm":       {"score": round(hmm_score, 2), "weight": 0.10, "null": False},
        "momentum":  {"score": round(mom_score, 2) if mom_score is not None else None, "weight": 0.35, "null": mom_score is None},
        "vol_trend": {"score": round(vt_score, 2)  if vt_score  is not None else None, "weight": 0.25, "null": vt_score  is None},
        "earnings":  {"score": round(earn_score, 2) if earn_score is not None else None, "weight": 0.20, "null": earn_score is None},
        "sentiment": {"score": round(sent_score, 2) if sent_score is not None else None, "weight": 0.00, "null": sent_score is None},
        "insider":   {"score": round(insider_score, 2) if insider_score is not None else None, "weight": 0.10, "null": insider_score is None},
    }

    available = {k: v for k, v in factors.items() if not v["null"]}
    total_w = sum(v["weight"] for v in available.values())
    composite = sum(v["score"] * v["weight"] / total_w for v in available.values()) if total_w > 0 else 0.0

    return {
        "ticker":         ticker,
        "factors":        factors,
        "composite_score": round(composite, 2),
        "hmm_signal":     sig["signal"],
        "hmm_confidence": sig["confidence"],
        "current_price":  float(closes[-1]),
        "volume_ok":      volume_ok,
        "overextended":   overextended,
    }


@app.get("/api/factors/{ticker}")
def get_factors(ticker: str):
    ticker = ticker.upper()
    # Surface HTTPException messages the same way as before
    try:
        fetch_ohlcv(ticker, days=10, min_bars=2)  # fast check for existence
    except HTTPException as e:
        return {"error": e.detail}
    result = _compute_factors(ticker)
    if result is None:
        return {"error": f"Failed to compute factors for '{ticker}'"}
    return {k: v for k, v in result.items() if k != "current_price"}


# ═══════════════════════════════════════════════════════════════════════════════
# ALTERNATIVE DATA
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/api/sentiment/{ticker}")
def get_sentiment(ticker: str):
    ticker = ticker.upper()
    api_key = os.getenv("ALPHA_VANTAGE_KEY", "")
    if not api_key:
        return {"available": False, "reason": "no_key"}

    cached, ts = _SENTIMENT_CACHE.get(ticker, ({}, 0.0))
    if cached and (time.time() - ts) < _SENTIMENT_TTL:
        return cached

    try:
        global _SENTIMENT_LAST_CALL
        with _SENTIMENT_LOCK:
            now = time.time()
            if now - _SENTIMENT_LAST_CALL < _SENTIMENT_MIN_INTERVAL:
                return {"available": False, "reason": "rate_limited"}
            _SENTIMENT_LAST_CALL = now

        url = (
            f"https://www.alphavantage.co/query"
            f"?function=NEWS_SENTIMENT&tickers={ticker}&apikey={api_key}"
        )
        with httpx.Client(timeout=15) as client:
            resp = client.get(url)
        if resp.status_code != 200:
            return {"available": False}
        data = resp.json()

        if "Note" in data or "Information" in data:
            return {"available": False, "reason": "rate_limited"}

        feed = data.get("feed", [])
        if not feed:
            return {"available": False}

        scores = [float(a["overall_sentiment_score"]) for a in feed if "overall_sentiment_score" in a]
        if not scores:
            return {"available": False}

        avg_score = sum(scores) / len(scores)
        sentiment_score = round((avg_score + 1) / 2 * 100, 1)

        label_map = {
            "Bullish": "bullish",
            "Somewhat-Bullish": "bullish",
            "Neutral": "neutral",
            "Somewhat-Bearish": "bearish",
            "Bearish": "bearish",
        }
        if sentiment_score >= 60:
            direction = "bullish"
        elif sentiment_score <= 40:
            direction = "bearish"
        else:
            direction = "neutral"

        bearish_count = sum(
            1 for a in feed
            if label_map.get(a.get("overall_sentiment_label", ""), "neutral") == "bearish"
        )
        bearish_pct = round(bearish_count / len(feed) * 100, 1)

        result = {
            "available": True,
            "ticker": ticker,
            "sentiment_score": sentiment_score,
            "direction": direction,
            "article_count": int(data.get("items", len(feed))),
            "buzz_score": None,
            "sector_vs_avg": None,
            "bearish_pct": bearish_pct,
        }
        _SENTIMENT_CACHE[ticker] = (result, time.time())
        return result
    except Exception as e:
        return {"error": str(e), "available": False}


@app.get("/api/insider/{ticker}")
def get_insider(ticker: str):
    ticker = ticker.upper()
    try:
        today = datetime.now().strftime("%Y-%m-%d")
        start = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
        url = (
            f"https://efts.sec.gov/LATEST/search-index?q=%22{ticker}%22"
            f"&dateRange=custom&startdt={start}&enddt={today}&forms=4"
        )
        headers = {"User-Agent": "stock-tracker emmettmacken@gmail.com"}
        with httpx.Client(timeout=15, follow_redirects=True) as client:
            resp = client.get(url, headers=headers)
        if resp.status_code != 200:
            return {"available": False}
        data = resp.json()
        hits = data.get("hits", {}).get("hits", [])
        if not hits:
            return {"available": False, "ticker": ticker, "net_shares": 0, "transaction_count": 0, "direction": "neutral"}

        net_shares = 0
        tx_count = 0
        for hit in hits:
            src = hit.get("_source", {})
            tx_type = src.get("transaction_type", "")
            shares_raw = src.get("shares", 0)
            try:
                shares = float(shares_raw) if shares_raw else 0
            except (ValueError, TypeError):
                shares = 0
            if tx_type == "P":
                net_shares += shares
                tx_count += 1
            elif tx_type == "S":
                net_shares -= shares
                tx_count += 1

        if net_shares > 0:
            direction = "buying"
        elif net_shares < 0:
            direction = "selling"
        else:
            direction = "neutral"

        return {
            "available": True,
            "ticker": ticker,
            "net_shares": int(net_shares),
            "transaction_count": tx_count,
            "direction": direction,
            "period_days": 30,
        }
    except Exception as e:
        return {"available": False, "error": str(e)}


@app.get("/api/shortinterest/{ticker}")
def get_short_interest(ticker: str):
    ticker = ticker.upper()
    try:
        url = f"https://finviz.com/quote.ashx?t={ticker}"
        headers = {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
        with httpx.Client(timeout=15, follow_redirects=True) as client:
            resp = client.get(url, headers=headers)
        if resp.status_code != 200:
            return {"available": False}

        soup = BeautifulSoup(resp.text, "html.parser")

        def _find_stat(label: str) -> str | None:
            cell = soup.find("td", string=label)
            if cell and cell.find_next_sibling("td"):
                return cell.find_next_sibling("td").get_text(strip=True)
            # Also check snapshot table pattern
            for td in soup.find_all("td"):
                if td.get_text(strip=True) == label:
                    nxt = td.find_next_sibling("td")
                    if nxt:
                        return nxt.get_text(strip=True)
            return None

        short_float_str = _find_stat("Short Float")
        short_ratio_str = _find_stat("Short Ratio")
        shares_short_str = _find_stat("Short Interest")

        def _parse_pct(s: str | None) -> float | None:
            if s is None:
                return None
            try:
                return float(s.replace("%", "").replace(",", ""))
            except ValueError:
                return None

        def _parse_float(s: str | None) -> float | None:
            if s is None:
                return None
            try:
                return float(s.replace(",", "").replace("M", "e6").replace("B", "e9"))
            except ValueError:
                return None

        short_float = _parse_pct(short_float_str)
        short_ratio = _parse_float(short_ratio_str)
        shares_short = _parse_float(shares_short_str)

        if short_float is None and short_ratio is None:
            return {"available": False}

        high_short = short_float is not None and short_float > 20.0
        return {
            "available": True,
            "ticker": ticker,
            "short_float_pct": short_float,
            "short_ratio": short_ratio,
            "shares_short": shares_short,
            "high_short_interest": high_short,
        }
    except Exception as e:
        return {"available": False, "error": str(e)}


# ═══════════════════════════════════════════════════════════════════════════════
# PORTFOLIO SIZING
# ═══════════════════════════════════════════════════════════════════════════════

class TickerSignal(BaseModel):
    composite_score: float
    confidence: float

class SizingRequest(BaseModel):
    capital: float
    tickers: list[str]
    signals: dict[str, TickerSignal]


def _realised_vol(closes: np.ndarray, days: int = 21) -> float:
    if len(closes) < days + 1:
        return 0.25  # default 25% annualised
    rets = np.diff(closes[-days - 1:]) / closes[-days - 1:-1]
    return float(rets.std() * np.sqrt(252))


def _kelly_fraction(composite_score: float, rvol: float) -> float:
    """Edge ~ linear map score 50→0, 100→0.3. Variance = rvol^2."""
    edge = max(0.0, (composite_score - 50.0) / 50.0 * 0.3)
    variance = max(rvol ** 2, 0.01)
    kelly = edge / variance
    return min(kelly * 0.5, 0.25)  # half-Kelly, cap at 25%


@app.post("/api/portfolio/sizing")
def portfolio_sizing(req: SizingRequest):
    try:
        tickers = [t.upper() for t in req.tickers]
        signals = {k.upper(): v for k, v in req.signals.items()}

        closes_map: dict[str, np.ndarray] = {}
        vols_map: dict[str, float] = {}
        for t in tickers:
            try:
                df = fetch_ohlcv(t, days=120, min_bars=30)
                c = df["Close"].values.astype(float)
                closes_map[t] = c
                vols_map[t] = _realised_vol(c)
            except Exception:
                vols_map[t] = 0.25
                closes_map[t] = np.array([])

        # Correlation matrix from 90-day returns
        ret_series: dict[str, pd.Series] = {}
        for t in tickers:
            c = closes_map.get(t, np.array([]))
            if len(c) >= 91:
                r = pd.Series(np.diff(c[-91:]) / c[-91:-1])
                ret_series[t] = r
        corr_matrix: dict[tuple, float] = {}
        if len(ret_series) >= 2:
            df_rets = pd.DataFrame(ret_series).dropna()
            if len(df_rets) >= 10:
                corr = df_rets.corr()
                for i, t1 in enumerate(tickers):
                    for j, t2 in enumerate(tickers):
                        if t1 in corr.index and t2 in corr.columns:
                            corr_matrix[(t1, t2)] = float(corr.loc[t1, t2])

        # Scores
        scores = {t: signals[t].composite_score if t in signals else 50.0 for t in tickers}
        sorted_tickers = sorted(tickers, key=lambda t: -scores[t])

        # Correlation penalty: reduce weight of correlated lower-scored tickers
        corr_penalties: dict[str, float] = {t: 1.0 for t in tickers}
        for i, t1 in enumerate(sorted_tickers):
            for t2 in sorted_tickers[:i]:  # t2 is higher-scored
                c_val = corr_matrix.get((t1, t2), corr_matrix.get((t2, t1), 0.0))
                if c_val > 0.7:
                    penalty = 1.0 - (c_val - 0.7) / 0.3 * 0.5
                    corr_penalties[t1] = min(corr_penalties[t1], penalty)

        # Kelly allocations
        kelly_fracs: dict[str, float] = {}
        for t in tickers:
            sig = signals.get(t)
            score = sig.composite_score if sig else 50.0
            rvol = vols_map[t]
            kf = _kelly_fraction(score, rvol) * corr_penalties[t]
            kelly_fracs[t] = kf

        # Vol-targeted allocations: 1/vol weight, normalised
        inv_vol = {t: (1.0 / max(vols_map[t], 0.01)) * corr_penalties[t] for t in tickers}
        total_inv_vol = sum(inv_vol.values())
        vol_alloc = {t: inv_vol[t] / total_inv_vol if total_inv_vol > 0 else 1.0 / len(tickers) for t in tickers}

        result: dict[str, Any] = {}
        for t in tickers:
            result[t] = {
                "kelly_fraction": round(kelly_fracs[t], 4),
                "kelly_dollar": round(kelly_fracs[t] * req.capital, 2),
                "vol_targeted_weight": round(vol_alloc[t], 4),
                "vol_targeted_dollar": round(vol_alloc[t] * req.capital, 2),
                "realised_vol_21d": round(vols_map[t], 4),
                "correlation_penalty": round(corr_penalties[t], 4),
            }
        return {"capital": req.capital, "tickers": tickers, "allocations": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ═══════════════════════════════════════════════════════════════════════════════
# PORTFOLIO BACKTEST
# ═══════════════════════════════════════════════════════════════════════════════

class PortfolioBacktestRequest(BaseModel):
    tickers: list[str]
    capital: float = 10000.0


@app.post("/api/portfolio/backtest")
def portfolio_backtest(req: PortfolioBacktestRequest):
    try:
        tickers = [t.upper() for t in req.tickers]
        TRAIN, TEST = 252, 21

        # Fetch all tickers + SPY
        all_tickers = list(set(tickers + ["SPY"]))
        dfs: dict[str, pd.DataFrame] = {}
        for t in all_tickers:
            try:
                df = fetch_ohlcv(t, days=760, min_bars=TRAIN + TEST + 5)
                dfs[t] = df
            except Exception:
                pass

        valid = [t for t in tickers if t in dfs]
        if not valid:
            raise HTTPException(status_code=400, detail="No valid tickers with sufficient history")

        # Align dates across all valid tickers
        close_dfs = {t: dfs[t]["Close"].rename(t) for t in valid}
        combined = pd.concat(close_dfs.values(), axis=1).dropna()
        if len(combined) < TRAIN + TEST:
            raise HTTPException(status_code=400, detail="Insufficient overlapping history across tickers")

        combined_arr = combined.values  # shape (T, N)
        dates_idx = combined.index
        n_days, n_stocks = combined_arr.shape

        # Precompute features per ticker
        features: dict[str, tuple] = {}
        for i, t in enumerate(valid):
            c = combined_arr[:, i]
            vol_series = dfs[t]["Volume"].reindex(dates_idx).ffill().values
            ret = np.diff(c) / c[:-1]
            vol_20d = pd.Series(vol_series).rolling(20, min_periods=1).mean().values
            vol_aligned = vol_series[1:]
            vol_20d_aligned = vol_20d[1:]
            features[t] = (c, ret, vol_aligned, vol_20d_aligned)

        # Walk-forward
        portfolio_val = req.capital
        equity_curve: list[dict] = []
        rebalance_events: list[dict] = []
        per_ticker_contrib: dict[str, float] = {t: 0.0 for t in valid}

        # SPY benchmark
        spy_closes = None
        spy_base = None
        if "SPY" in dfs:
            spy_aligned = dfs["SPY"]["Close"].reindex(dates_idx).ffill()
            spy_closes = spy_aligned.values

        test_start = TRAIN
        while test_start + TEST <= n_days - 1:
            ts = test_start - TRAIN
            te = test_start

            # Compute vol-targeted weights at this rebalance point
            rvols: dict[str, float] = {}
            signals_window: dict[str, str] = {}
            for t in valid:
                c, ret, vol, vol_20d = features[t]
                tr_rets = ret[ts:te]
                tr_vol  = vol[ts:te]
                tr_20d  = vol_20d[ts:te]
                if len(tr_rets) < 22:
                    rvols[t] = 0.25
                    signals_window[t] = "HOLD"
                    continue
                rvols[t] = tr_rets[-21:].std() * np.sqrt(252)
                tr_states, lo_t, hi_t = make_state_seq(tr_rets, tr_vol, tr_20d)
                mat_full, _, cnt_full = build_matrix(tr_states)
                stat_full = stationary(mat_full)
                ratios = tr_vol / np.maximum(tr_20d, 1.0)
                cur_st = si(ret_bucket(float(tr_rets[-1])), vol_bucket(float(ratios[-1]), lo_t, hi_t))
                sig = compute_signal(cnt_full[cur_st], mat_full[cur_st], stat_full)
                signals_window[t] = sig["signal"]

            # Vol-targeted weights (only allocate to BUY signals)
            buy_tickers = [t for t in valid if signals_window[t] == "BUY"]
            if not buy_tickers:
                buy_tickers = valid  # fallback: equal weight
            inv_vol = {t: 1.0 / max(rvols.get(t, 0.25), 0.01) for t in buy_tickers}
            total_inv = sum(inv_vol.values())
            weights = {t: inv_vol[t] / total_inv for t in buy_tickers}
            for t in valid:
                if t not in weights:
                    weights[t] = 0.0

            rebalance_date = str(dates_idx[test_start])[:10]
            rebalance_events.append({
                "date": rebalance_date,
                "weights": {t: round(weights[t], 4) for t in valid},
                "signals": signals_window,
            })

            # Simulate TEST days with these weights
            for idx in range(test_start, min(test_start + TEST, n_days - 1)):
                contrib_sum = 0.0
                for i, t in enumerate(valid):
                    c = combined_arr[:, i]
                    if idx + 1 < len(c) and c[idx] > 0:
                        r_t = (c[idx + 1] - c[idx]) / c[idx]
                        contrib = weights.get(t, 0.0) * r_t
                        per_ticker_contrib[t] += contrib
                        contrib_sum += contrib
                portfolio_val *= (1.0 + contrib_sum)

                spy_val = None
                if spy_closes is not None:
                    if spy_base is None:
                        spy_base = spy_closes[test_start]
                    if idx + 1 < len(spy_closes) and spy_base > 0:
                        spy_val = round(spy_closes[idx + 1] / spy_base * req.capital, 2)

                raw_date = dates_idx[idx + 1]
                date_str = raw_date.strftime("%Y-%m-%d") if hasattr(raw_date, "strftime") else str(raw_date)[:10]
                equity_curve.append({
                    "date": date_str,
                    "value": round(portfolio_val, 2),
                    "spy": spy_val,
                })

            test_start += TEST

        # Performance metrics
        vals = np.array([p["value"] for p in equity_curve])
        total_return = (portfolio_val - req.capital) / req.capital * 100

        spy_return = None
        if spy_closes is not None and spy_base is not None and spy_base > 0:
            spy_return = round((spy_closes[min(test_start, len(spy_closes) - 1)] / spy_base - 1.0) * 100, 2)

        daily_rets = np.diff(vals) / vals[:-1] if len(vals) > 1 else np.array([0.0])
        sharpe = float(daily_rets.mean() / daily_rets.std() * np.sqrt(252)) if daily_rets.std() > 1e-10 else 0.0
        peaks = np.maximum.accumulate(vals)
        max_dd = float(((vals - peaks) / np.maximum(peaks, 1e-9)).min() * 100) if len(vals) > 0 else 0.0

        # Monte Carlo efficient frontier (500 random weightings)
        n_mc = 500
        ef_points: list[dict] = []
        if n_stocks > 1 and len(equity_curve) > 10:
            combined_rets = np.diff(combined_arr, axis=0) / combined_arr[:-1]
            mu = combined_rets.mean(axis=0)
            cov = np.cov(combined_rets.T)
            rng = np.random.default_rng(42)
            for _ in range(n_mc):
                w = rng.dirichlet(np.ones(n_stocks))
                port_ret = float(np.dot(w, mu) * 252 * 100)
                port_vol = float(np.sqrt(w @ cov @ w) * np.sqrt(252) * 100)
                ef_points.append({"return": round(port_ret, 3), "volatility": round(port_vol, 3)})

        return {
            "tickers": valid,
            "capital": req.capital,
            "equity_curve": equity_curve,
            "total_return_pct": round(total_return, 2),
            "spy_return_pct": spy_return,
            "sharpe_ratio": round(sharpe, 3),
            "max_drawdown_pct": round(max_dd, 2),
            "per_ticker_contrib": {t: round(v * 100, 3) for t, v in per_ticker_contrib.items()},
            "rebalance_events": rebalance_events,
            "efficient_frontier": ef_points,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ═══════════════════════════════════════════════════════════════════════════════
# AUTOMATION HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _get_alpaca():
    """Return configured Alpaca TradingClient or (None, reason_str)."""
    if not ALPACA_OK:
        return None, "alpaca-py not installed"
    key = os.getenv("ALPACA_API_KEY", "")
    secret = os.getenv("ALPACA_SECRET_KEY", "")
    if not key or not secret:
        return None, "ALPACA_API_KEY / ALPACA_SECRET_KEY not set in .env"
    try:
        return TradingClient(api_key=key, secret_key=secret, paper=True), None
    except Exception as e:
        return None, str(e)


def _compute_atr(ticker: str, period: int = 21) -> float:
    """Average True Range over `period` days."""
    try:
        df = yf.Ticker(ticker).history(period="3mo")
        if len(df) < period + 1:
            return 0.0
        h, l, c = df["High"].values, df["Low"].values, df["Close"].values
        tr = np.maximum(h[1:] - l[1:], np.maximum(np.abs(h[1:] - c[:-1]), np.abs(l[1:] - c[:-1])))
        return float(tr[-period:].mean())
    except Exception:
        return 0.0


def _macro_regime() -> tuple[bool, float]:
    """Returns (spy_above_200d_ma, vix_level)."""
    spy_above = True
    try:
        h = yf.Ticker("SPY").history(period="1y")
        if len(h) >= 200:
            spy_above = float(h["Close"].iloc[-1]) > float(h["Close"].iloc[-200:].mean())
    except Exception:
        pass
    vix = 20.0
    try:
        h = yf.Ticker("^VIX").history(period="5d")
        if not h.empty:
            vix = float(h["Close"].iloc[-1])
    except Exception:
        pass
    return spy_above, vix


def _earnings_within_days(ticker: str, days: int = 2) -> bool:
    """Return True if earnings announcement is within `days` calendar days."""
    try:
        cal = yf.Ticker(ticker).calendar
        if cal is None:
            return False
        dates = []
        if isinstance(cal, dict):
            raw = cal.get("Earnings Date", [])
            dates = raw if isinstance(raw, list) else [raw]
        elif hasattr(cal, "columns") and "Earnings Date" in cal.columns:
            dates = cal["Earnings Date"].tolist()
        now = datetime.now()
        for d in dates:
            try:
                dt = d.replace(tzinfo=None) if hasattr(d, "tzinfo") else datetime.fromisoformat(str(d))
                if abs((dt - now).days) <= days:
                    return True
            except Exception:
                pass
        return False
    except Exception:
        return False


def _position_dollars(ticker: str, equity: float, current_price: float, score: float = 75.0) -> float:
    """Vol-targeted position size scaled by conviction score, capped at 15% of equity."""
    try:
        df = fetch_ohlcv(ticker, days=30, min_bars=22)
        c = df["Close"].values.astype(float)
        rets_21 = np.diff(c[-22:]) / c[-22:-1]
        daily_vol = float(rets_21.std())
        if daily_vol > 0:
            weight = min(0.01 / daily_vol, 0.10)
        else:
            weight = 0.05
    except Exception:
        weight = 0.05
    # Conviction multiplier: score 75→1×, 85→1.25×, 95→1.5× (capped)
    multiplier = min(1.0 + max(0.0, score - 75.0) / 40.0, 1.5)
    return weight * equity * multiplier


def _trading_days_between(start: datetime, end: datetime) -> int:
    try:
        return int(np.busday_count(start.date(), end.date()))
    except Exception:
        return max(0, int((end - start).days * 5 / 7))


def _close_and_record(api, ticker: str, current_price: float, entry_price: float,
                      exit_reason: str, entry_log: Optional[dict]) -> None:
    """Close an Alpaca position and write trade_outcomes."""
    api.close_position(ticker)
    ret = (current_price - entry_price) / entry_price * 100
    hold = 0
    if entry_log:
        try:
            hold = _trading_days_between(
                datetime.fromisoformat(entry_log["timestamp"]), datetime.now()
            )
        except Exception:
            pass
    db.log_signal(ticker, None, "SELL", "closed", exit_reason, current_price, 0.0)
    db.record_trade(
        ticker,
        entry_log["id"] if entry_log else None,
        entry_price, current_price, exit_reason,
        ret, hold,
        entry_log.get("composite_score") if entry_log else None,
    )
    logger.info("%s: closed (%s) at %.2f (%.1f%%)", ticker, exit_reason, current_price, ret)


# ── Scheduled jobs ────────────────────────────────────────────────────────────

def _run_signal_job() -> None:
    logger.info("▶ Signal job starting")
    api, err = _get_alpaca()
    if api is None:
        logger.warning("Signal job aborted: %s", err)
        return

    et_now    = datetime.now(ZoneInfo("America/New_York"))
    is_friday = et_now.weekday() == 4

    watchlist = [r["ticker"] for r in db.get_watchlist()]
    if not watchlist:
        logger.info("Signal job: watchlist empty")
        return

    try:
        account   = api.get_account()
        equity    = float(account.equity)
        positions = {p.symbol: p for p in api.get_all_positions()}
    except Exception as e:
        logger.error("Signal job: Alpaca account fetch failed: %s", e)
        return

    spy_above, vix = _macro_regime()
    high_vix      = vix > 30
    buy_threshold = 85.0 if not spy_above else 75.0
    logger.info("Macro: SPY>200d=%s VIX=%.1f threshold=%.0f friday=%s",
                spy_above, vix, buy_threshold, is_friday)

    # Sector counts of currently open positions
    open_sector_counts: dict[str, int] = {}
    for sym in positions:
        sec = _get_sector(sym)
        open_sector_counts[sec] = open_sector_counts.get(sec, 0) + 1

    for ticker in watchlist:
        try:
            if _earnings_within_days(ticker):
                db.log_signal(ticker, None, None, "skipped", "earnings_within_2d", None, None)
                logger.info("%s: skipped (earnings soon)", ticker)
                continue

            result = _compute_factors(ticker)
            if result is None:
                db.log_signal(ticker, None, None, "skipped", "data_unavailable", None, None)
                continue

            composite  = result["composite_score"]
            hmm_signal = result["hmm_signal"]
            price      = result["current_price"]
            atr        = _compute_atr(ticker)
            in_pos     = ticker in positions

            # Score deterioration — close regardless of HMM signal
            if in_pos and composite < 40.0:
                pos = positions[ticker]
                entry_log = db.get_last_buy_signal(ticker)
                try:
                    _close_and_record(api, ticker, price, float(pos.avg_entry_price),
                                      "score_deterioration", entry_log)
                    db.log_signal(ticker, composite, hmm_signal, "closed",
                                  "score_deterioration", price, atr)
                except Exception as e:
                    db.log_signal(ticker, composite, hmm_signal, "skipped",
                                  f"close_failed:{e}", price, atr)
                continue

            if hmm_signal == "SELL" and composite < 45.0 and in_pos:
                pos = positions[ticker]
                entry_log = db.get_last_buy_signal(ticker)
                try:
                    _close_and_record(api, ticker, price, float(pos.avg_entry_price),
                                      "sell_signal", entry_log)
                    db.log_signal(ticker, composite, "SELL", "closed", "sell_signal", price, atr)
                except Exception as e:
                    db.log_signal(ticker, composite, "SELL", "skipped",
                                  f"close_failed:{e}", price, atr)

            elif hmm_signal == "BUY" and composite >= buy_threshold:
                if is_friday:
                    db.log_signal(ticker, composite, "BUY", "skipped",
                                  "friday_no_entry", price, atr)
                    continue
                if high_vix:
                    db.log_signal(ticker, composite, "BUY", "skipped",
                                  f"vix_too_high:{vix:.1f}", price, atr)
                    continue
                if in_pos:
                    db.log_signal(ticker, composite, "BUY", "skipped",
                                  "already_in_position", price, atr)
                    continue
                if not result.get("volume_ok", True):
                    db.log_signal(ticker, composite, "BUY", "skipped", "low_volume", price, atr)
                    continue
                if result.get("overextended", False):
                    db.log_signal(ticker, composite, "BUY", "skipped", "overextended", price, atr)
                    continue
                sector = _get_sector(ticker)
                if sector != "Unknown" and open_sector_counts.get(sector, 0) >= 2:
                    db.log_signal(ticker, composite, "BUY", "skipped",
                                  "sector_concentration", price, atr)
                    continue

                dollars = _position_dollars(ticker, equity, price, composite)
                try:
                    try:
                        api.submit_order(MarketOrderRequest(
                            symbol=ticker, notional=round(dollars, 2),
                            side=OrderSide.BUY, time_in_force=TimeInForce.DAY))
                    except Exception:
                        qty = max(1, int(dollars // price))
                        api.submit_order(MarketOrderRequest(
                            symbol=ticker, qty=qty,
                            side=OrderSide.BUY, time_in_force=TimeInForce.DAY))
                    signal_id = db.log_signal(ticker, composite, "BUY", "ordered", None, price, atr)
                    if atr > 0:
                        db.update_trailing_stop(signal_id, price - 1.5 * atr)
                    open_sector_counts[sector] = open_sector_counts.get(sector, 0) + 1
                    logger.info("%s: BUY $%.0f score=%.1f sector=%s", ticker, dollars, composite, sector)
                except Exception as e:
                    db.log_signal(ticker, composite, "BUY", "skipped",
                                  f"order_failed:{e}", price, atr)
            else:
                db.log_signal(ticker, composite, hmm_signal, "skipped",
                              "hold_or_below_threshold", price, atr)
        except Exception as e:
            logger.error("Signal job error for %s: %s", ticker, e)

    logger.info("◀ Signal job done")


def _run_stoploss_job() -> None:
    logger.info("▶ Stop-loss job starting")
    api, err = _get_alpaca()
    if api is None:
        logger.warning("Stop-loss job aborted: %s", err)
        return

    try:
        positions = api.get_all_positions()
    except Exception as e:
        logger.error("Stop-loss job: failed to list positions: %s", e)
        return

    now = datetime.now()
    for pos in positions:
        ticker = pos.symbol
        try:
            price       = float(pos.current_price)
            entry_price = float(pos.avg_entry_price)
            entry_log   = db.get_last_buy_signal(ticker)
            if not entry_log:
                continue
            atr_entry = entry_log.get("atr_at_signal") or 0.0
            hold_days = _trading_days_between(
                datetime.fromisoformat(entry_log["timestamp"]), now
            )

            # Trailing stop: raise the floor as price rises
            current_atr = _compute_atr(ticker)
            atr_for_stop = current_atr if current_atr > 0 else atr_entry
            stored_stop  = entry_log.get("current_stop")
            if stored_stop is None and atr_entry > 0:
                stored_stop = entry_price - 1.5 * atr_entry
            if atr_for_stop > 0:
                candidate = price - 1.5 * atr_for_stop
                if stored_stop is None or candidate > stored_stop:
                    stored_stop = candidate
                    db.update_trailing_stop(entry_log["id"], stored_stop)

            exit_reason = None
            if stored_stop is not None and price < stored_stop:
                exit_reason = "stop_loss"
            elif hold_days > 21:
                exit_reason = "max_hold_exit"
            if exit_reason:
                try:
                    _close_and_record(api, ticker, price, entry_price, exit_reason, entry_log)
                except Exception as e:
                    logger.error("Stop-loss close failed %s: %s", ticker, e)
        except Exception as e:
            logger.error("Stop-loss check error for %s: %s", ticker, e)

    logger.info("◀ Stop-loss job done")


# ═══════════════════════════════════════════════════════════════════════════════
# WATCHLIST API
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/api/watchlist")
def api_get_watchlist():
    return db.get_watchlist()


@app.post("/api/watchlist/{ticker}", status_code=201)
def api_add_ticker(ticker: str):
    ticker = ticker.upper()
    db.add_ticker(ticker)
    return {"ticker": ticker, "status": "added"}


@app.delete("/api/watchlist/{ticker}")
def api_remove_ticker(ticker: str):
    ticker = ticker.upper()
    db.remove_ticker(ticker)
    # Cancel any open Alpaca orders for this ticker
    api, _ = _get_alpaca()
    if api:
        try:
            for order in api.get_orders(GetOrdersRequest(status=QueryOrderStatus.OPEN)):
                if order.symbol == ticker:
                    api.cancel_order_by_id(order.id)
        except Exception as e:
            logger.warning("Could not cancel orders for %s: %s", ticker, e)
    return {"ticker": ticker, "status": "removed"}


# ═══════════════════════════════════════════════════════════════════════════════
# PAPER TRADING API
# ═══════════════════════════════════════════════════════════════════════════════

def _alpaca_or_error(label: str):
    """Return (api, None) or raise HTTPException with a clear message."""
    api, err = _get_alpaca()
    if api is None:
        raise HTTPException(status_code=503, detail=f"{label}: {err}")
    return api


@app.get("/api/paper/account")
def api_paper_account():
    api, err = _get_alpaca()
    if api is None:
        return {"available": False, "error": err}
    try:
        acc = api.get_account()
        n_pos = len(api.get_all_positions())
        return {
            "available":      True,
            "equity":         round(float(acc.equity), 2),
            "cash":           round(float(acc.cash), 2),
            "buying_power":   round(float(acc.buying_power), 2),
            "positions_count": n_pos,
        }
    except Exception as e:
        return {"available": False, "error": str(e)}


@app.get("/api/paper/positions")
def api_paper_positions():
    api, err = _get_alpaca()
    if api is None:
        return {"available": False, "error": err}
    try:
        positions = api.get_all_positions()
    except Exception as e:
        return {"available": False, "error": str(e)}

    result = []
    now = datetime.now()
    for pos in positions:
        ticker      = pos.symbol
        entry_price = float(pos.avg_entry_price)
        curr_price  = float(pos.current_price)
        pnl_pct     = float(pos.unrealized_plpc) * 100

        entry_log = db.get_last_buy_signal(ticker)
        atr_entry     = (entry_log.get("atr_at_signal") or 0.0) if entry_log else 0.0
        composite     = (entry_log.get("composite_score")) if entry_log else None
        trailing_stop = (entry_log.get("current_stop")) if entry_log else None
        # Fall back to fixed ATR stop if trailing stop hasn't been set yet
        if trailing_stop is None and atr_entry:
            trailing_stop = entry_price - 1.5 * atr_entry
        hold_days = 0
        if entry_log:
            try:
                hold_days = _trading_days_between(
                    datetime.fromisoformat(entry_log["timestamp"]), now
                )
            except Exception:
                pass

        result.append({
            "ticker":          ticker,
            "entry_price":     round(entry_price, 4),
            "current_price":   round(curr_price, 4),
            "pnl_pct":         round(pnl_pct, 2),
            "composite_score": composite,
            "atr_stop":        round(entry_price - 1.5 * atr_entry, 4) if atr_entry else None,
            "trailing_stop":   round(trailing_stop, 4) if trailing_stop is not None else None,
            "days_held":       hold_days,
            "qty":             float(pos.qty),
            "market_value":    round(float(pos.market_value), 2),
        })
    return {"available": True, "positions": result}


@app.get("/api/paper/history")
def api_paper_history():
    return db.get_trade_history()


@app.get("/api/signals/log")
def api_signal_log(limit: int = 50):
    return db.get_signal_log(limit)


@app.post("/api/paper/run-now")
def api_run_now():
    """Trigger the signal job immediately in a background thread."""
    t = threading.Thread(target=_run_signal_job, daemon=True)
    t.start()
    return {"status": "started", "message": "Signal job running in background — check /api/signals/log for results"}
