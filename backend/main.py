"""Stock Signal Tracker v2 — 2D Markov chain, HMM regimes, CI signals, walk-forward backtest."""
from __future__ import annotations
import json
import math
import re
import warnings
import time
import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import Any, Optional
from xml.etree import ElementTree as ET
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import yfinance as yf
from yfinance.exceptions import YFTzMissingError, YFRateLimitError, YFTickerMissingError
from statsmodels.stats.proportion import proportion_confint
from hmmlearn import hmm
import httpx
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

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

# Module-level Alpaca singleton — initialized once at import time after load_dotenv()
_ak, _sk = os.getenv("ALPACA_API_KEY", ""), os.getenv("ALPACA_SECRET_KEY", "")
if ALPACA_OK and _ak and _sk:
    try:
        _alpaca_client: "TradingClient | None" = TradingClient(api_key=_ak, secret_key=_sk, paper=True)
        _alpaca_err: "str | None" = None
    except Exception as _e:
        _alpaca_client = None
        _alpaca_err = str(_e)
elif not ALPACA_OK:
    _alpaca_client = None
    _alpaca_err = "alpaca-py not installed"
else:
    _alpaca_client = None
    _alpaca_err = "ALPACA_API_KEY / ALPACA_SECRET_KEY not set in .env"
del _ak, _sk

import database as db

warnings.filterwarnings("ignore", category=DeprecationWarning)
warnings.filterwarnings("ignore", category=UserWarning)


def _sanitize_json(obj):
    """Recursively replace NaN floats with None for JSON serialization."""
    if isinstance(obj, float) and math.isnan(obj):
        return None
    if isinstance(obj, dict):
        return {k: _sanitize_json(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_json(v) for v in obj]
    return obj
warnings.filterwarnings("ignore", message=".*convergence.*")
warnings.filterwarnings("ignore", message=".*not converging.*")
warnings.filterwarnings("ignore", message=".*Model is not.*")
logging.getLogger("hmmlearn").setLevel(logging.ERROR)

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
            CronTrigger(day_of_week="mon-fri", hour=15, minute=30, timezone=ET),
            id="signal_job", replace_existing=True,
        )
        _scheduler.add_job(
            _run_stoploss_job,
            CronTrigger(day_of_week="mon-fri", hour=9, minute=35, timezone=ET),
            id="stoploss_job", replace_existing=True,
        )
        _scheduler.add_job(
            _run_adaptive_thresholds_job,
            CronTrigger(day_of_week="sun", hour=18, minute=0, timezone=ET),
            id="adaptive_thresholds_job", replace_existing=True,
        )
        _scheduler.start()
        logger.info("Scheduler started (signal@15:30 ET, stop-loss@09:35 ET, thresholds@Sun18:00 ET)")
    else:
        logger.warning("APScheduler not available — scheduled jobs disabled")


@app.on_event("shutdown")
def _shutdown() -> None:
    if SCHEDULER_OK and _scheduler is not None and _scheduler.running:
        _scheduler.shutdown(wait=False)
    try:
        _http_client.close()
    except Exception:
        pass

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

# Change 5: one lock per cache dict to prevent TOCTOU races during parallel factor computation
_FACTORS_LOCK = threading.Lock()
_MACRO_LOCK = threading.Lock()
_EARNINGS_LOCK = threading.Lock()
_SECTOR_LOCK = threading.Lock()

# Shared httpx client — connection pooling, avoid per-request TLS handshakes
_http_client = httpx.Client(timeout=15.0, follow_redirects=True)

# Per-ticker factor cache (60s TTL — avoids triple yfinance call per signal job tick)
_FACTORS_CACHE: dict[str, tuple[dict, float]] = {}
_FACTORS_TTL = 60  # seconds

# Change 3: CIKs are permanent; cache indefinitely to avoid repeated EDGAR lookups
_CIK_CACHE: dict[str, str] = {}  # ticker → 10-digit zero-padded CIK string

# ── Factor weight configuration ───────────────────────────────────────────────

# Change 2: sentiment added at 12%; earnings reduced 25→18%, insider 10→5% to keep total=100%
DEFAULT_FACTOR_WEIGHTS: dict[str, float] = {
    "hmm":       0.20,
    "momentum":  0.25,
    "vol_trend": 0.20,
    "earnings":  0.18,
    "insider":   0.05,
    "sentiment": 0.12,
}

_WEIGHT_OVERRIDES_PATH = os.path.join(os.path.dirname(__file__), "factor_weight_overrides.json")
_WEIGHT_DRIFT_LOG_PATH = os.path.join(os.path.dirname(__file__), "weight_overrides.json")
_FACTOR_CORR_PATH      = os.path.join(os.path.dirname(__file__), "factor_correlations.json")
_GATE_STATS_PATH       = os.path.join(os.path.dirname(__file__), "gate_stats.json")


def _load_ticker_weights(ticker: str) -> dict[str, float]:
    """Return weights for ticker, merging per-ticker overrides over defaults."""
    try:
        with open(_WEIGHT_OVERRIDES_PATH) as f:
            overrides: dict = json.load(f)
        per_ticker = overrides.get(ticker.upper(), {})
    except (FileNotFoundError, json.JSONDecodeError):
        per_ticker = {}
    weights = {**DEFAULT_FACTOR_WEIGHTS, **per_ticker}
    if per_ticker:
        drifts = {
            k: {
                "override": round(v, 4),
                "default":  round(DEFAULT_FACTOR_WEIGHTS.get(k, 0.0), 4),
                "delta":    round(v - DEFAULT_FACTOR_WEIGHTS.get(k, 0.0), 4),
            }
            for k, v in per_ticker.items()
            if abs(v - DEFAULT_FACTOR_WEIGHTS.get(k, 0.0)) > 0.05
        }
        if drifts:
            _append_weight_drift(ticker, drifts)
    return weights


def _append_weight_drift(ticker: str, drifts: dict) -> None:
    try:
        with open(_WEIGHT_DRIFT_LOG_PATH) as f:
            log: dict = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        log = {}
    log[ticker.upper()] = {"logged_at": datetime.utcnow().isoformat(), "drifts": drifts}
    with open(_WEIGHT_DRIFT_LOG_PATH, "w") as f:
        json.dump(log, f, indent=2)


# Earnings calendar cache (24h TTL — earnings dates don't change intraday)
_EARNINGS_CACHE: dict[str, tuple[bool, float]] = {}
_EARNINGS_TTL = 86400  # 24 hours

# Macro regime cache (5-min TTL — SPY/VIX checked once per signal job, not per ticker)
_MACRO_CACHE: Optional[tuple] = None  # ((spy_above, vix), timestamp)
_MACRO_TTL = 300  # seconds

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
    if len(states) > 1:
        states_arr = np.array(states, dtype=int)
        np.add.at(counts, (states_arr[:-1], states_arr[1:]), 1)
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
        new_pi = pi @ mat
        if np.max(np.abs(new_pi - pi)) < 1e-10:
            return new_pi
        pi = new_pi
    return pi

# ── HMM regime detection ──────────────────────────────────────────────────────

def _kalman_smooth(raw: np.ndarray, Q: float = 0.01, R: float = 0.1) -> np.ndarray:
    """1-D scalar Kalman filter for smoothing a 0-1 probability series."""
    n = len(raw)
    if n == 0:
        return raw.copy()
    x = 0.5       # initial state estimate
    P = 1.0       # initial error covariance
    out = np.empty(n)
    for i in range(n):
        P_pred = P + Q
        K = P_pred / (P_pred + R)
        x = x + K * (float(raw[i]) - x)
        P = (1.0 - K) * P_pred
        out[i] = x
    return out


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


def _normalize_returns_for_hmm(returns: np.ndarray, window: int = 63) -> np.ndarray:
    """
    Normalize returns against rolling mean and std so the HMM sees directional
    deviation from trend rather than absolute level.  Prevents the volatility-split
    failure mode on steadily trending stocks where both HMM states end up positive.
    Only used as input to fit_regimes / tr_model.predict — never for other factors.
    """
    if len(returns) < window + 1:
        return returns
    normed = np.empty_like(returns)
    for i in range(len(returns)):
        start = max(0, i - window)
        window_rets = returns[start:i] if i > 0 else returns[0:1]
        if len(window_rets) < 5:
            normed[i] = returns[i]
            continue
        mu = np.mean(window_rets)
        sigma = np.std(window_rets)
        normed[i] = (returns[i] - mu) / sigma if sigma > 1e-8 else 0.0
    return normed


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

    if n_total < MIN_OBS:
        return {
            "signal":            "HOLD",
            "confidence":        0.3,
            "bullish_edge":      0.0,
            "bearish_edge":      0.0,
            "bull_edge_ci_low":  0.0,
            "bull_edge_ci_high": 0.0,
            "bear_edge_ci_low":  0.0,
            "bear_edge_ci_high": 0.0,
            "n_obs_current_state": n_total,
        }
    b_lo, b_hi = proportion_confint(n_bull, n_total, alpha=0.05, method="wilson")
    r_lo, r_hi = proportion_confint(n_bear, n_total, alpha=0.05, method="wilson")

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
            return df[["High", "Low", "Close", "Volume"]].dropna()
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

def _wilder_atr(tr: np.ndarray, period: int = 21) -> np.ndarray:
    """Wilder's EMA of a true-range series, seeded with the SMA of the first `period`
    TRs. Returns an array the same length as `tr`; indices before the seed are NaN."""
    n = len(tr)
    out = np.full(n, np.nan)
    if n < period:
        return out
    atr = float(tr[:period].mean())
    out[period - 1] = atr
    alpha = 1.0 / period
    for i in range(period, n):
        atr = alpha * float(tr[i]) + (1.0 - alpha) * atr
        out[i] = atr
    return out


def _atr_from_df(df: pd.DataFrame, period: int = 21) -> float:
    """Compute ATR using Wilder's EMA (seeded with SMA over first `period` bars)."""
    if len(df) < period + 1:
        return 0.0
    h, l, c = df["High"].values, df["Low"].values, df["Close"].values
    tr = np.maximum(h[1:] - l[1:], np.maximum(np.abs(h[1:] - c[:-1]), np.abs(l[1:] - c[:-1])))
    series = _wilder_atr(tr, period)
    last = series[-1]
    return float(last) if not np.isnan(last) else 0.0


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
    hmm_returns = _normalize_returns_for_hmm(returns)

    # Fit HMM on full 2-year return series
    try:
        regime_seq, bull_id, _ = fit_regimes(hmm_returns)
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
    hmm_returns = _normalize_returns_for_hmm(returns)
    dates = df.index.tolist()[1:]
    n = len(returns)  # = len(closes) - 1

    TRAIN = int(db.get_config("BACKTEST_TRAIN", "252"))
    TEST  = int(db.get_config("BACKTEST_TEST", "21"))
    # Fix 2-E: align with live system — 21-day max hold and ATR trailing stop on by default
    HOLD  = int(db.get_config("BACKTEST_HOLD", "21"))
    atr_mult     = float(db.get_config("BACKTEST_ATR_MULTIPLIER", "1.5"))
    use_atr_stop = db.get_config("BACKTEST_ATR_STOP", "true").lower() == "true"
    macro_filter = db.get_config("BACKTEST_MACRO_FILTER", "true").lower() == "true"

    if n < TRAIN + TEST:
        raise HTTPException(status_code=400, detail="Need at least 2 years of history for backtest")

    # Fix 2-E: Wilder 21-period ATR series (same logic as live _atr_from_df), aligned
    # with returns (length n). Early indices are NaN but idx starts at TRAIN (252) so
    # they're never read; the loop falls back to 2% of price when ATR is NaN/<=0.
    h_bt  = df["High"].values.astype(float)[1:]
    l_bt  = df["Low"].values.astype(float)[1:]
    pc_bt = closes[:-1]
    tr_bt = np.maximum(h_bt - l_bt, np.maximum(np.abs(h_bt - pc_bt), np.abs(l_bt - pc_bt)))
    atr_series = _wilder_atr(tr_bt, 21)

    # Change 1: precompute VIX history aligned to ticker dates for the VIX>30 gate
    vix_series_bt: Optional[np.ndarray] = None
    try:
        vix_df_bt = yf.Ticker("^VIX").history(period="2y")
        if not vix_df_bt.empty:
            vix_aligned = vix_df_bt["Close"].reindex(df.index).ffill().bfill()
            vix_series_bt = vix_aligned.values.astype(float)[1:]  # length n, aligned with returns
    except Exception:
        vix_series_bt = None  # fail open: skip VIX gate if data unavailable

    # Change 1: precompute SPY 200-day MA for macro filter (one SPY fetch, no in-loop calls)
    spy_above_ma: Optional[np.ndarray] = None
    if macro_filter:
        try:
            spy_df = fetch_ohlcv("SPY", days=760, min_bars=260)
            spy_arr = spy_df["Close"].reindex(df.index).ffill().bfill().values.astype(float)
            spy_ma200 = pd.Series(spy_arr).rolling(200, min_periods=1).mean().values
            spy_above_ma = (spy_arr > spy_ma200)
        except Exception:
            spy_above_ma = None  # fail open: don't block trades on SPY fetch failure

    # Change 1: rolling MA20 for overextension gate
    ma20_series = pd.Series(closes).rolling(20, min_periods=1).mean().values

    # Change 1: rolling 63-day returns for top-quartile momentum override on overextension
    ret_63d_bt = np.full(len(closes), np.nan)
    for _i in range(63, len(closes)):
        ret_63d_bt[_i] = closes[_i] / closes[_i - 63] - 1.0
    mom_q75_bt = pd.Series(ret_63d_bt).rolling(252, min_periods=63).quantile(0.75).values

    # Change 1: pre-compute 3m/12m momentum for momentum-disagreement gate (skip 21 days)
    ret_3m_bt = np.full(len(closes), np.nan)
    ret_12m_bt = np.full(len(closes), np.nan)
    for _i in range(len(closes)):
        _ci = _i - 21
        if _ci >= 63:
            ret_3m_bt[_i] = closes[_ci] / closes[_ci - 63] - 1.0
        if _ci >= 252:
            ret_12m_bt[_i] = closes[_ci] / closes[_ci - 252] - 1.0

    # Change 1: 7bps commission + 0.1% slippage per trade side = 0.17% per side
    TC_PER_SIDE = 0.0017

    # Walk-forward simulation
    portfolio   = 1.0
    bah_base    = float(closes[TRAIN])
    in_pos      = False
    hold_left   = 0
    trail_stop  = 0.0
    daily_strat: list[float] = []
    equity_curve: list[dict] = []
    trade_results: list[bool] = []
    trade_entry_val = 0.0
    last_exit_idx = -100  # Change 1: tracks re-entry cooldown (5 trading days)

    # Change 1: gate rejection counters for comparison output
    gate_rejections: dict[str, int] = {
        "vix_too_high": 0,
        "overextended": 0,
        "momentum_disagreement": 0,
        "reentry_cooldown": 0,
    }
    total_buy_signals_raw = 0  # HMM=BUY signals before any gate

    test_start = TRAIN
    while test_start + TEST <= n:
        ts, te = test_start - TRAIN, test_start

        tr_rets, tr_vol, tr_20d = returns[ts:te], vol[ts:te], vol_20d[ts:te]
        tr_states, lo_t, hi_t = make_state_seq(tr_rets, tr_vol, tr_20d)

        mat_full, _, cnt_full = build_matrix(tr_states)
        stat_full = stationary(mat_full)

        # Fit HMM on training window only — no future data in model parameters
        try:
            tr_regime_seq, tr_bull_id, tr_model = fit_regimes(hmm_returns[ts:te])
            test_len = min(TEST, n - te)
            test_regime_seq = tr_model.predict(hmm_returns[te:te + test_len].reshape(-1, 1))
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
                atr_exit = False
                if use_atr_stop:
                    cur_price = closes[idx]
                    atr_now = atr_series[idx] if atr_series[idx] > 0 else cur_price * 0.02
                    candidate = cur_price - atr_mult * atr_now
                    trail_stop = max(trail_stop, candidate)
                    if cur_price < trail_stop:
                        portfolio *= (1.0 - TC_PER_SIDE)  # Change 1: exit transaction cost
                        trade_results.append(portfolio > trade_entry_val)
                        in_pos = False
                        last_exit_idx = idx
                        atr_exit = True
                if atr_exit:
                    daily_strat.append(0.0)
                else:
                    portfolio *= (1 + r_t)
                    daily_strat.append(r_t)
                    hold_left -= 1
                    # Change 1: mid-window exit on SELL signal (mirrors live score deterioration gate)
                    if signal == "SELL" or hold_left <= 0:
                        portfolio *= (1.0 - TC_PER_SIDE)  # Change 1: exit transaction cost
                        trade_results.append(portfolio > trade_entry_val)
                        in_pos = False
                        last_exit_idx = idx
            else:
                daily_strat.append(0.0)
                if signal == "BUY":
                    spy_ok = (spy_above_ma is None) or bool(spy_above_ma[idx])
                    if not macro_filter or spy_ok:
                        total_buy_signals_raw += 1

                        # Change 1: VIX > 30 gate
                        if vix_series_bt is not None and idx < len(vix_series_bt):
                            if vix_series_bt[idx] > 30.0:
                                gate_rejections["vix_too_high"] += 1
                                continue

                        # Change 1: overextension gate (price > 1.25×MA20 unless top-quartile momentum)
                        if idx < len(ma20_series) and ma20_series[idx] > 0:
                            price_ratio = closes[idx] / ma20_series[idx]
                            top_q_mom = (
                                not np.isnan(ret_63d_bt[idx]) and
                                not np.isnan(mom_q75_bt[idx]) and
                                ret_63d_bt[idx] >= mom_q75_bt[idx]
                            )
                            if price_ratio > 1.25 and not top_q_mom:
                                gate_rejections["overextended"] += 1
                                continue

                        # Change 1: momentum disagreement gate
                        r3  = ret_3m_bt[idx]
                        r12 = ret_12m_bt[idx]
                        if not np.isnan(r3) and not np.isnan(r12):
                            if (r3 + r12) <= 0 or r3 < -0.10 or r12 < -0.10:
                                gate_rejections["momentum_disagreement"] += 1
                                continue

                        # Change 1: re-entry cooldown (5 trading days after last exit)
                        if idx - last_exit_idx < 5:
                            gate_rejections["reentry_cooldown"] += 1
                            continue

                        in_pos = True
                        hold_left = HOLD
                        portfolio *= (1.0 - TC_PER_SIDE)  # Change 1: entry transaction cost
                        trade_entry_val = portfolio
                        if use_atr_stop:
                            atr_entry = atr_series[idx] if atr_series[idx] > 0 else closes[idx] * 0.02
                            trail_stop = closes[idx] - atr_mult * atr_entry

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

    win_rate_trades = float(sum(trade_results) / len(trade_results) * 100) if trade_results else 0.0

    trades_after_gates = total_buy_signals_raw - sum(gate_rejections.values())
    return {
        "ticker":                  ticker,
        "equity_curve":            equity_curve,
        "total_strategy_return":   round(strat_pct, 2),
        "total_bah_return":        round(bah_pct, 2),
        "sharpe_ratio":            round(sharpe, 3),
        "max_drawdown":            round(max_dd, 2),
        "win_rate_trades":         round(win_rate_trades, 1),
        "num_trades":              len(trade_results),
        "num_windows":             (n - TRAIN) // TEST,
        # Change 1: gate comparison — how many BUY signals each gate filtered
        "gate_comparison": {
            "total_buy_signals_raw":     total_buy_signals_raw,
            "trades_after_gates":        trades_after_gates,
            "filters":                   gate_rejections,
            "transaction_cost_bps_per_side": int(TC_PER_SIDE * 10000),
            "notes": (
                "earnings_within_2d and sector_cap gates omitted: require real-time data "
                "not available in historical simulation."
            ),
        },
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
    return 50.0 + (confidence - 0.65) * 20.0  # HOLD: uncertainty→43, mid→50, high-conf→57


def _momentum_score(closes: np.ndarray) -> float | None:
    """3m+12m momentum, skip last 21 days, each horizon z-scored against its own distribution."""
    if len(closes) < 252 + 21 + 2:
        return None
    c = closes[:-21]
    m3  = (c[-1] / c[-63]  - 1.0) if len(c) >= 63  else None
    m12 = (c[-1] / c[-252] - 1.0) if len(c) >= 252 else None
    if m3 is None and m12 is None:
        return None

    z_scores: list[float] = []
    if m3 is not None:
        w3 = np.array([closes[i] / closes[i - 63] - 1.0 for i in range(63, len(closes) - 21)])
        mu3, sig3 = float(w3.mean()), float(w3.std())
        z_scores.append((m3 - mu3) / sig3 if sig3 > 1e-10 else 0.0)
    if m12 is not None:
        w12 = np.array([closes[i] / closes[i - 252] - 1.0 for i in range(252, len(closes) - 21)])
        mu12, sig12 = float(w12.mean()), float(w12.std())
        z_scores.append((m12 - mu12) / sig12 if sig12 > 1e-10 else 0.0)

    if not z_scores:
        return None
    z = float(np.clip(float(np.mean(z_scores)), -3, 3))
    return (z + 3) / 6 * 100


def _vol_trend_score(closes: np.ndarray) -> tuple[float | None, dict | None]:
    """Return (0–100 score, display detail). Detail is the raw price/MA levels so the
    UI can show which moving averages the price is above/below — display only."""
    if len(closes) < 201:
        return None, None
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
    detail = {
        "price": float(price),
        "ma20":  float(ma20),
        "ma50":  float(ma50),
        "ma200": float(ma200),
    }
    return float(np.clip(score, 0, 100)), detail


def _earnings_score(ticker_obj: yf.Ticker) -> tuple[float | None, dict | None]:
    """Return (0–100 score, display detail). Detail carries the last two quarters'
    surprise % (act vs estimate, as fractions) so the UI can show the raw beats/misses."""
    try:
        eh = ticker_obj.earnings_history
        if eh is None or len(eh) < 2:
            return None, None
        # Most recent two quarters
        last2 = eh.sort_index().tail(2)
        surprises = []
        for col in ["epsActual", "epsEstimate"]:
            if col not in last2.columns:
                return None, None
        for _, row in last2.iterrows():
            est, act = row.get("epsEstimate"), row.get("epsActual")
            if pd.isna(est) or pd.isna(act) or est == 0:
                return None, None
            surprises.append((act - est) / abs(est))
        if len(surprises) < 2:
            return None, None
        detail = {"surprises": [float(s) for s in surprises]}  # oldest → newest
        pos = sum(s > 0 for s in surprises)
        if pos == 2:
            avg = np.mean(surprises)
            return float(np.clip(70.0 + avg * 200.0, 70.0, 100.0)), detail
        if pos == 0:
            avg = np.mean(surprises)
            return float(np.clip(30.0 + avg * 200.0, 0.0, 30.0)), detail
        return 50.0, detail
    except Exception as e:
        logger.warning("Earnings score failed for %s: %s", getattr(ticker_obj, "ticker", "?"), e)
        return None, None


def _get_sentiment_score(ticker: str) -> float | None:
    """Return 0–100 sentiment score from cache or Alpha Vantage.
    Shares the module-level rate limiter (_SENTIMENT_LAST_CALL) with the API endpoint
    so parallel calls from the signal job are serialized to ≤1 req/13s."""
    api_key = os.getenv("ALPHA_VANTAGE_KEY", "")
    if not api_key:
        return None
    global _SENTIMENT_LAST_CALL
    with _SENTIMENT_LOCK:
        cached, ts = _SENTIMENT_CACHE.get(ticker, ({}, 0.0))
        if cached and cached.get("available") and (time.time() - ts) < _SENTIMENT_TTL:
            return cached.get("sentiment_score")
        now = time.time()
        if now - _SENTIMENT_LAST_CALL < _SENTIMENT_MIN_INTERVAL:
            return None  # rate-limited; caller should retry after the interval
        _SENTIMENT_LAST_CALL = now
    try:
        url = (
            f"https://www.alphavantage.co/query"
            f"?function=NEWS_SENTIMENT&tickers={ticker}&apikey={api_key}"
        )
        resp = _http_client.get(url)
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
        result_entry = {
            "available": True, "ticker": ticker, "sentiment_score": score, "direction": direction,
            "article_count": int(data.get("items", len(feed))), "buzz_score": None,
            "sector_vs_avg": None, "bearish_pct": None,
        }
        with _SENTIMENT_LOCK:
            _SENTIMENT_CACHE[ticker] = (result_entry, time.time())
        return score
    except Exception as e:
        logger.warning("Sentiment fetch failed for %s: %s", ticker, e)
        return None


def _get_cik(ticker: str) -> Optional[str]:
    """Return 10-digit zero-padded SEC CIK for a ticker, cached indefinitely."""
    if ticker in _CIK_CACHE:
        return _CIK_CACHE[ticker]
    headers = {"User-Agent": "stock-tracker emmettmacken@gmail.com"}
    try:
        url = (
            f"https://www.sec.gov/cgi-bin/browse-edgar"
            f"?action=getcompany&ticker={ticker}&type=4&dateb=&owner=include"
            f"&count=10&search_text=&output=atom"
        )
        resp = _http_client.get(url, headers=headers, timeout=10.0)
        if resp.status_code != 200:
            return None
        m = re.search(r"CIK=(\d{1,10})[&\"']", resp.text)
        if not m:
            return None
        cik = m.group(1).zfill(10)
        _CIK_CACHE[ticker] = cik
        return cik
    except Exception as e:
        logger.warning("CIK lookup failed for %s: %s", ticker, e)
        return None


def _get_insider_score(ticker: str) -> float | None:
    """
    Change 3: 0-100 insider score from EDGAR structured submissions API (not EFTS full-text).
    Fetches Form 4 filings for the exact company CIK to avoid false positives.
    P = open-market purchase (bullish), S = open-market sale (bearish) — awards/exercises excluded.
    Filer weight: Officer=1.0×, Director=0.7×, 10%+ holder=0.5×, other=0.3×.
    Returns None (not 50) on EDGAR failure so the weight drops and renormalises.
    """
    ROLE_WEIGHTS = {"isOfficer": 1.0, "isDirector": 0.7, "isTenPercentOwner": 0.5}
    headers = {"User-Agent": "stock-tracker emmettmacken@gmail.com"}
    try:
        cik = _get_cik(ticker)
        if not cik:
            return None

        sub_url = f"https://data.sec.gov/submissions/CIK{cik}.json"
        resp = _http_client.get(sub_url, headers=headers, timeout=15.0)
        if resp.status_code != 200:
            return None

        data = resp.json()
        recent = data.get("filings", {}).get("recent", {})
        forms        = recent.get("form", [])
        dates        = recent.get("filingDate", [])
        accessions   = recent.get("accessionNumber", [])
        primary_docs = recent.get("primaryDocument", [])

        cutoff = (datetime.now() - timedelta(days=30)).date()

        # Collect Form 4s within the 30-day lookback; filings are sorted newest-first
        recent_form4s: list[tuple[str, str]] = []
        for i, form in enumerate(forms):
            if form != "4":
                continue
            fd_str = dates[i] if i < len(dates) else ""
            try:
                fd = datetime.strptime(fd_str, "%Y-%m-%d").date()
            except Exception:
                continue
            if fd < cutoff:
                break  # past window, no need to scan further
            acc = accessions[i] if i < len(accessions) else ""
            doc = primary_docs[i] if i < len(primary_docs) else ""
            if acc and doc:
                recent_form4s.append((acc, doc))

        if not recent_form4s:
            # Fix 2-H: no Form 4s = no data, not a neutral signal. Return None so the factor
            # is excluded from the composite and the remaining weights renormalise.
            return None

        cik_int = int(cik)
        net_weighted = 0.0
        filings_processed = 0

        for acc, primary_doc in recent_form4s[:10]:  # cap to avoid excessive HTTP calls
            acc_clean = acc.replace("-", "")
            # primaryDocument may have an XSL prefix (e.g. "xslF345X06/form4.xml");
            # the raw XML lives at the basename path without the XSL subdirectory
            xml_name = primary_doc.split("/")[-1] if "/" in primary_doc else primary_doc
            xml_url = (
                f"https://www.sec.gov/Archives/edgar/data/{cik_int}/"
                f"{acc_clean}/{xml_name}"
            )
            try:
                xml_resp = _http_client.get(xml_url, headers=headers, timeout=10.0)
                if xml_resp.status_code != 200:
                    continue
                tree = ET.fromstring(xml_resp.text)

                # Highest role weight for this filer (take max if multiple roles held)
                role_weight = 0.3
                for role_tag, w in ROLE_WEIGHTS.items():
                    el = tree.find(f".//reportingOwnerRelationship/{role_tag}")
                    if el is not None and (el.text or "").strip() in ("1", "true"):
                        role_weight = max(role_weight, w)

                # Non-derivative transactions only; skip awards (A), exercises (M), gifts (G), etc.
                for tx in tree.findall(".//nonDerivativeTransaction"):
                    code_el   = tx.find(".//transactionCoding/transactionCode")
                    shares_el = tx.find(".//transactionAmounts/transactionShares/value")
                    if code_el is None or shares_el is None:
                        continue
                    code = (code_el.text or "").strip()
                    if code not in ("P", "S"):
                        continue
                    try:
                        shares = float(shares_el.text or 0)
                    except (ValueError, TypeError):
                        continue
                    net_weighted += shares * role_weight if code == "P" else -shares * role_weight

                filings_processed += 1
            except Exception:
                continue

        if filings_processed == 0:
            return None  # Form 4s exist but all failed to parse → drop weight, don't guess

        return 70.0 if net_weighted > 0 else (30.0 if net_weighted < 0 else 50.0)
    except Exception as e:
        logger.warning("Insider score failed for %s: %s", ticker, e)
        return None


def _get_sector(ticker: str) -> str:
    """Return the sector string for a ticker, cached for 24 h."""
    # Change 5: lock prevents TOCTOU race during parallel signal job
    with _SECTOR_LOCK:
        cached_sector, ts = _SECTOR_CACHE.get(ticker, ("", 0.0))
        if cached_sector and (time.time() - ts) < _SECTOR_TTL:
            return cached_sector
    try:
        sector = yf.Ticker(ticker).info.get("sector", "Unknown") or "Unknown"
    except Exception as e:
        logger.warning("Sector lookup failed for %s: %s", ticker, e)
        sector = "Unknown"
    with _SECTOR_LOCK:
        _SECTOR_CACHE[ticker] = (sector, time.time())
    return sector


def _compute_factors(ticker: str, force: bool = False) -> Optional[dict]:
    """Core factor computation shared by the API endpoint and the scheduler.

    Pass force=True to bypass the read cache (used by the explicit single-ticker
    refresh) so the user always gets freshly computed numbers.
    """
    # Change 5: lock prevents two concurrent threads from both finding cache stale
    if not force:
        with _FACTORS_LOCK:
            cached_result, cached_ts = _FACTORS_CACHE.get(ticker, ({}, 0.0))
            if cached_result and (time.time() - cached_ts) < _FACTORS_TTL:
                return cached_result

    try:
        df = fetch_ohlcv(ticker, days=760, min_bars=260)
    except Exception:
        return None

    closes, returns, vol, vol_20d = extract_features(df)
    hmm_returns = _normalize_returns_for_hmm(returns)

    smoothed_bull_prob_last = 0.5
    raw_bull_prob_last = 0.5
    hmm_fit_failed = False
    try:
        regime_seq, bull_id, hmm_model = fit_regimes(hmm_returns)
        proba = hmm_model.predict_proba(hmm_returns.reshape(-1, 1))
        raw_bull_prob_series = proba[:, bull_id]
        smoothed_bull_prob_series = _kalman_smooth(raw_bull_prob_series)
        raw_bull_prob_last = float(raw_bull_prob_series[-1])
        smoothed_bull_prob_last = float(smoothed_bull_prob_series[-1])
    except Exception as e:
        # Fix 2-D: surface (don't swallow) HMM failure — bull_prob=0.5 here is a
        # fallback, not a genuine neutral reading; flag it so it's visible downstream.
        hmm_fit_failed = True
        logger.warning("HMM fit/predict failed for %s: %s — falling back to single regime, bull_prob=0.5", ticker, e)
        regime_seq = np.zeros(len(returns), dtype=int)
        bull_id = 0

    # Regime label based on Kalman-smoothed probability (keeps raw hard label for matrix math)
    if smoothed_bull_prob_last > 0.65:
        regime_label = "bull"
    elif smoothed_bull_prob_last < 0.35:
        regime_label = "bear"
    else:
        regime_label = "transition"

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
    # Fix 2-C: use the last *complete* bar [-2] for the current Markov state, consistent with
    # the volume read below — today's bar is partial at signal time. Fall back to [-1] if short.
    _cb = -2 if len(rets_w) >= 2 else -1
    cur_st = si(ret_bucket(float(rets_w[_cb])), vol_bucket(float(ratios_w[_cb]), lo_t, hi_t))
    sig = compute_signal(cnt_act[cur_st], mat_act[cur_st], stat_act)

    hmm_score      = _hmm_factor_score(sig["signal"], sig["confidence"])
    mom_score      = _momentum_score(closes)
    vt_score, vt_detail     = _vol_trend_score(closes)
    earn_score, earn_detail = _earnings_score(yf.Ticker(ticker))
    insider_score  = _get_insider_score(ticker)
    # Change 2: sentiment included as a factor; null → weight dropped and renormalised
    sentiment_score = _get_sentiment_score(ticker)

    # Volume and overextension signals used by the signal job
    # Use [-2] (last *complete* trading day) — today's bar is partial at signal time (15:30 ET)
    current_vol  = float(vol[-2])     if len(vol)     >= 2 else (float(vol[-1])     if len(vol)     > 0 else 0.0)
    avg_vol_20d  = float(vol_20d[-2]) if len(vol_20d) >= 2 else (float(vol_20d[-1]) if len(vol_20d) > 0 else 1.0)
    volume_ratio = round(current_vol / avg_vol_20d, 3) if avg_vol_20d > 0 else None
    vol_thresh   = float(db.get_config("VOLUME_THRESHOLD", "1.05"))
    volume_ok    = current_vol > vol_thresh * avg_vol_20d if avg_vol_20d > 0 else True
    ma20         = float(closes[-20:].mean()) if len(closes) >= 20 else float(closes[-1])
    price_ma20_ratio = float(closes[-1]) / ma20 if ma20 > 0 else 1.0
    # Fix 2-F: derive overextended from the same config the live gate uses (no hardcoded 1.25)
    _oe_thresh_pct = float(db.get_config("OVEREXTENDED_THRESHOLD_PCT", "0.25"))
    overextended = price_ma20_ratio > (1.0 + _oe_thresh_pct)

    # Fix 2-B: true rolling 75th-percentile of 63-day returns, matching the backtest's
    # overextension override (ret_63d >= mom_q75) instead of the z-scored mom_score threshold.
    if len(closes) > 63:
        ret_63d_arr = closes[63:] / closes[:-63] - 1.0
    else:
        ret_63d_arr = np.array([])
    if len(ret_63d_arr) >= 63:
        mom_q75_now = float(np.quantile(ret_63d_arr[-252:], 0.75))
        top_quartile_mom = bool(float(ret_63d_arr[-1]) >= mom_q75_now)
    else:
        top_quartile_mom = False

    weights = _load_ticker_weights(ticker)

    factors: dict[str, Any] = {
        "hmm":       {"score": round(hmm_score, 2), "weight": weights["hmm"], "null": False},
        "momentum":  {"score": round(mom_score, 2)      if mom_score      is not None else None, "weight": weights["momentum"],  "null": mom_score      is None},
        "vol_trend": {"score": round(vt_score, 2)        if vt_score       is not None else None, "weight": weights["vol_trend"], "null": vt_score       is None},
        "earnings":  {"score": round(earn_score, 2)      if earn_score     is not None else None, "weight": weights["earnings"],  "null": earn_score     is None},
        "insider":   {"score": round(insider_score, 2)   if insider_score  is not None else None, "weight": weights["insider"],   "null": insider_score  is None},
        # Change 2: sentiment factor; null when API unavailable — weight renormalises automatically
        "sentiment": {"score": round(sentiment_score, 2) if sentiment_score is not None else None, "weight": weights.get("sentiment", 0.12), "null": sentiment_score is None},
    }

    available = {k: v for k, v in factors.items() if not v["null"]}
    total_w = sum(v["weight"] for v in available.values())
    composite = sum(v["score"] * v["weight"] / total_w for v in available.values()) if total_w > 0 else 0.0

    non_null_scores = [v["score"] for v in factors.values() if not v["null"] and v["score"] is not None]
    min_factor_score = min(non_null_scores) if non_null_scores else None

    c_lag   = closes[:-21] if len(closes) > 21 else closes
    ret_3m  = float(c_lag[-1] / c_lag[-63]  - 1.0) if len(c_lag) >= 63  else None
    ret_12m = float(c_lag[-1] / c_lag[-252] - 1.0) if len(c_lag) >= 252 else None

    # Display-only: day-over-day price change for the snapshot/homepage cards.
    prev_close = float(closes[-2]) if len(closes) >= 2 else float(closes[-1])
    price_change_pct = (
        round((float(closes[-1]) - prev_close) / prev_close * 100, 4) if prev_close else 0.0
    )

    atr = _atr_from_df(df)
    rets_21 = np.diff(closes[-22:]) / closes[-22:-1] if len(closes) >= 22 else np.array([0.0])
    vol_21d = float(rets_21.std())

    result = {
        "ticker":              ticker,
        "factors":             factors,
        "composite_score":     round(composite, 2),
        "min_factor_score":    round(min_factor_score, 2) if min_factor_score is not None else None,
        "hmm_signal":          sig["signal"],
        "hmm_confidence":      sig["confidence"],
        "hmm_regime":          regime_label,
        "smoothed_bull_prob":  round(smoothed_bull_prob_last, 4),
        "raw_bull_prob":       round(raw_bull_prob_last, 4),
        "hmm_fit_failed":      hmm_fit_failed,
        "current_price":       float(closes[-1]),
        "price_change_pct":    price_change_pct,
        "volume_ok":           volume_ok,
        "volume_ratio":        volume_ratio,
        "overextended":        overextended,
        "price_ma20_ratio":    round(price_ma20_ratio, 4),
        "top_quartile_mom":    top_quartile_mom,
        "mom_score":           round(mom_score, 2) if mom_score is not None else None,
        "ret_3m":              ret_3m,
        "ret_12m":             ret_12m,
        # Display-only raw breakdowns for the stock detail page (never used in scoring)
        "vol_trend_detail":    vt_detail,
        "earnings_detail":     earn_detail,
        "atr":                 atr,
        "vol_21d":             vol_21d,
        "sentiment_score":     round(sentiment_score, 2) if sentiment_score is not None else None,
    }
    with _FACTORS_LOCK:
        _FACTORS_CACHE[ticker] = (result, time.time())
    return result


def _factors_payload(result: dict) -> dict:
    """The display payload (FactorScoreData) — full breakdown minus internal-only fields."""
    return {k: v for k, v in result.items() if k not in ("current_price", "atr", "vol_21d")}


def _write_snapshot(ticker: str, result: dict) -> None:
    """Persist a ticker's freshly computed factors as a cached display snapshot.

    Display-only: never affects trading logic. `signal` mirrors the Markov/HMM signal
    already shown on the card (BUY/SELL/HOLD).
    """
    db.upsert_snapshot(
        ticker=ticker,
        composite_score=result.get("composite_score"),
        signal=result.get("hmm_signal"),
        hmm_regime=result.get("hmm_regime"),
        price=result.get("current_price"),
        price_change_pct=result.get("price_change_pct"),
        factors=_factors_payload(result),
    )


@app.get("/api/factor-weights")
def get_factor_weights():
    """Return the active default factor weights as percentages (0–100)."""
    return {k: round(v * 100) for k, v in DEFAULT_FACTOR_WEIGHTS.items()}


@app.get("/api/factors/cluster")
def get_factors_cluster(tickers: str = ""):
    ticker_list = [t.strip().upper() for t in tickers.split(",") if t.strip()]
    if not ticker_list:
        return {"error": "Pass ?tickers=AAPL,MSFT,GOOG"}
    results = []
    for ticker in ticker_list:
        try:
            res = _compute_factors(ticker)
            if res is None:
                results.append({"ticker": ticker, "error": "compute returned None"})
                continue
            results.append({
                "ticker":           ticker,
                "composite_score":  res["composite_score"],
                "hmm_signal":       res["hmm_signal"],
                "min_factor_score": res.get("min_factor_score"),
                "volume_ratio":     res.get("volume_ratio"),
                "volume_ok":        res.get("volume_ok"),
                "factors_summary":  {
                    k: (v["score"] if not v["null"] else None)
                    for k, v in res["factors"].items()
                },
            })
        except Exception as e:
            results.append({"ticker": ticker, "error": str(e)})
    results.sort(key=lambda x: x.get("composite_score") or 0, reverse=True)
    return {"tickers": results, "count": len(results)}


@app.get("/api/factors/{ticker}")
def get_factors(ticker: str):
    ticker = ticker.upper()
    result = _compute_factors(ticker)
    if result is None:
        return {"error": f"Failed to compute factors for '{ticker}'"}
    # Live-compute path (also used by "Refresh all") — keep the display snapshot in sync.
    try:
        _write_snapshot(ticker, result)
    except Exception as e:
        logger.warning("snapshot write failed for %s: %s", ticker, e)
    return _factors_payload(result)


@app.get("/api/factor-correlations")
def get_factor_correlations():
    """Cross-sectional Pearson correlation of factor scores across the current watchlist."""
    watchlist = [r["ticker"] for r in db.get_watchlist()]
    if len(watchlist) < 3:
        return {"error": "Need at least 3 tickers for correlation"}
    factor_keys = ["hmm", "momentum", "vol_trend", "earnings", "insider", "sentiment"]
    rows: dict[str, list] = {k: [] for k in factor_keys}
    tickers_used: list[str] = []
    for ticker in watchlist:
        res = _compute_factors(ticker)
        if res is None:
            continue
        fdata = res["factors"]
        if any(fdata[k]["null"] for k in factor_keys):
            continue
        for k in factor_keys:
            rows[k].append(fdata[k]["score"])
        tickers_used.append(ticker)
    if len(tickers_used) < 3:
        return {"error": "Not enough tickers with complete factor data"}
    try:
        zero_variance = [k for k, v in rows.items() if len(set(v)) <= 1]
        corr_df = pd.DataFrame(rows).corr()
        raw_dict = corr_df.to_dict()
        corr_dict = {
            outer_k: {
                inner_k: (round(v, 4) if isinstance(v, float) and not math.isnan(v) else None)
                for inner_k, v in inner_v.items()
            }
            for outer_k, inner_v in raw_dict.items()
        }
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": f"Correlation computation failed: {e}"})
    out = {
        "computed_at":  datetime.utcnow().isoformat(),
        "tickers_used": tickers_used,
        "n":            len(tickers_used),
        "note":         "Cross-sectional correlation across watchlist at computation time",
        "correlations": corr_dict,
        "zero_variance_factors": zero_variance,
    }
    with open(_FACTOR_CORR_PATH, "w") as f:
        json.dump(out, f, indent=2)
    db.save_diagnostic("factor_correlations", out)
    return _sanitize_json(out)


@app.get("/api/gate-stats")
def get_gate_stats():
    try:
        with open(_GATE_STATS_PATH) as f:
            return _sanitize_json(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    db_data = db.load_diagnostic("gate_stats")
    if db_data:
        return _sanitize_json(db_data)
    return {"error": "No gate stats found. Run signals job first."}


@app.get("/api/weight-overrides")
def get_weight_overrides():
    try:
        with open(_WEIGHT_DRIFT_LOG_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        return {"error": str(e)}


@app.get("/api/debug")
def get_debug():
    out: dict = {}
    for path, key in [
        (_GATE_STATS_PATH,       "gate_stats"),
        (_FACTOR_CORR_PATH,      "factor_correlations"),
        (_WEIGHT_DRIFT_LOG_PATH, "weight_overrides"),
    ]:
        try:
            with open(path) as f:
                out[key] = _sanitize_json(json.load(f))
        except (FileNotFoundError, json.JSONDecodeError) as e:
            out[key] = {"error": str(e)}
    for key in ("gate_stats", "factor_correlations"):
        if "error" in out.get(key, {}):
            db_data = db.load_diagnostic(key)
            if db_data:
                out[key] = _sanitize_json(db_data)
    out["active_config"] = {
        "MIN_FACTOR_FLOOR":           db.get_config("MIN_FACTOR_FLOOR", "disabled"),
        "OVEREXTENDED_THRESHOLD_PCT": db.get_config("OVEREXTENDED_THRESHOLD_PCT", "0.25"),
        "DEFAULT_FACTOR_WEIGHTS":     DEFAULT_FACTOR_WEIGHTS,
    }
    return out


# ═══════════════════════════════════════════════════════════════════════════════
# ALTERNATIVE DATA
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/api/debug/sentiment")
def debug_sentiment():
    """Call _get_sentiment_score() for every watchlist ticker sequentially.
    Returns raw scores, null count, and null rate for sentiment health checking.
    Note: sequential calls with 13s gap; response may take several minutes for large watchlists."""
    watchlist = [r["ticker"] for r in db.get_watchlist()]
    results = []
    null_count = 0
    for ticker in watchlist:
        with _SENTIMENT_LOCK:
            cached, ts = _SENTIMENT_CACHE.get(ticker, ({}, 0.0))
            cache_fresh = cached and cached.get("available") and (time.time() - ts) < _SENTIMENT_TTL
        score = _get_sentiment_score(ticker)
        results.append({"ticker": ticker, "score": score, "null": score is None, "from_cache": cache_fresh})
        if score is None:
            null_count += 1
        if not cache_fresh:
            time.sleep(_SENTIMENT_MIN_INTERVAL)
    total = len(results)
    null_rate = round(null_count / total, 4) if total > 0 else 0.0
    return {
        "scores": results,
        "null_count": null_count,
        "null_rate": null_rate,
        "total": total,
        "api_key_present": bool(os.getenv("ALPHA_VANTAGE_KEY", "")),
        "rate_limit_interval_s": _SENTIMENT_MIN_INTERVAL,
    }


@app.get("/api/debug/kelly")
def debug_kelly():
    """Return current Kelly parameters for every watchlist ticker plus the portfolio-wide prior."""
    watchlist = [r["ticker"] for r in db.get_watchlist()]
    all_trades = db.get_all_trades_for_kelly()

    def _params(trades: list[dict]) -> Optional[dict]:
        result = _compute_kelly_params(trades)
        if result is None:
            return None
        p, b, f_star = result
        return {
            "p":          round(p, 4),
            "q":          round(1 - p, 4),
            "b":          round(b, 4),
            "f_star":     round(f_star, 4),
            "half_kelly": round(f_star * 0.5, 4),
            "n_trades":   len(trades),
        }

    portfolio_params = _params(all_trades)

    tickers_data = []
    for ticker in watchlist:
        ticker_trades = db.get_trades_for_kelly(ticker)
        if len(ticker_trades) >= 10:
            params = _params(ticker_trades)
            method = "kelly"
        elif len(all_trades) >= 20:
            params = portfolio_params
            method = "kelly_portfolio_prior"
        else:
            params = None
            method = "vol_target_fallback"
        tickers_data.append({
            "ticker":          ticker,
            "sizing_method":   method,
            "n_ticker_trades": len(ticker_trades),
            "kelly_params":    params,
        })

    return {
        "portfolio_prior":          portfolio_params,
        "total_portfolio_trades":   len(all_trades),
        "min_ticker_trades_for_kelly": 10,
        "min_portfolio_trades_for_prior": 20,
        "tickers":                  tickers_data,
    }


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
        resp = _http_client.get(url)
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
        resp = _http_client.get(url, headers=headers)
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
        resp = _http_client.get(url, headers=headers)
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

        # Normalize so total Kelly allocation never exceeds 100%
        total_kelly = sum(kelly_fracs.values())
        if total_kelly > 1.0:
            kelly_fracs = {t: v / total_kelly for t, v in kelly_fracs.items()}

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

        # Fetch all tickers + SPY in parallel
        all_tickers = list(set(tickers + ["SPY"]))
        dfs: dict[str, pd.DataFrame] = {}

        def _fetch_one(t: str):
            try:
                return t, fetch_ohlcv(t, days=760, min_bars=TRAIN + TEST + 5)
            except Exception:
                return t, None

        with ThreadPoolExecutor(max_workers=min(8, len(all_tickers))) as pool:
            for t, df in pool.map(_fetch_one, all_tickers):
                if df is not None:
                    dfs[t] = df

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

        # Change 1: 7bps commission + 0.1% slippage per side
        TC_PER_SIDE_PB = 0.0017

        # Walk-forward
        portfolio_val = req.capital
        equity_curve: list[dict] = []
        rebalance_events: list[dict] = []
        per_ticker_contrib: dict[str, float] = {t: 0.0 for t in valid}
        mid_window_exits = 0  # Change 1: counter for SELL-signal early exits

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
            # Change 1: also store per-ticker matrices for mid-window exit signal checking
            rvols: dict[str, float] = {}
            signals_window: dict[str, str] = {}
            ticker_mat_pb: dict[str, np.ndarray] = {}
            ticker_cnt_pb: dict[str, np.ndarray] = {}
            ticker_stat_pb: dict[str, np.ndarray] = {}
            ticker_lo_pb: dict[str, float] = {}
            ticker_hi_pb: dict[str, float] = {}

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
                ticker_mat_pb[t]  = mat_full
                ticker_cnt_pb[t]  = cnt_full
                ticker_stat_pb[t] = stat_full
                ticker_lo_pb[t]   = lo_t
                ticker_hi_pb[t]   = hi_t
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

            # Change 1: apply entry transaction costs on invested positions
            invested_weight = sum(weights[t] for t in valid if weights[t] > 0)
            if invested_weight > 0:
                portfolio_val *= (1.0 - TC_PER_SIDE_PB * invested_weight)

            rebalance_date = str(dates_idx[test_start])[:10]
            rebalance_events.append({
                "date": rebalance_date,
                "weights": {t: round(weights[t], 4) for t in valid},
                "signals": signals_window,
            })

            # Change 1: live_weights tracks positions that haven't been exited mid-window
            live_weights = dict(weights)

            # Simulate TEST days with these weights
            for idx in range(test_start, min(test_start + TEST, n_days - 1)):
                # Change 1: check for mid-window SELL exits using training-period matrices
                for t in valid:
                    if live_weights.get(t, 0.0) <= 0:
                        continue
                    if t not in ticker_mat_pb:
                        continue
                    _, ret_t, vol_t, vol_20d_t = features[t]
                    if idx < 1 or idx - 1 >= len(ret_t):
                        continue
                    r_prev  = float(ret_t[idx - 1])
                    v_prev  = float(vol_t[idx - 1])
                    v20_prev = float(vol_20d_t[idx - 1])
                    vr = v_prev / max(v20_prev, 1.0)
                    lo = ticker_lo_pb[t]
                    hi = ticker_hi_pb[t]
                    cur_st = si(ret_bucket(r_prev), vol_bucket(vr, lo, hi))
                    mat_d  = ticker_mat_pb[t]
                    cnt_d  = ticker_cnt_pb[t]
                    stat_d = ticker_stat_pb[t]
                    sig_d  = compute_signal(cnt_d[cur_st], mat_d[cur_st], stat_d)
                    if sig_d["signal"] == "SELL":
                        # Apply exit cost proportional to this position's weight
                        portfolio_val *= (1.0 - TC_PER_SIDE_PB * live_weights[t])
                        live_weights[t] = 0.0
                        mid_window_exits += 1

                contrib_sum = 0.0
                for i, t in enumerate(valid):
                    c = combined_arr[:, i]
                    if idx + 1 < len(c) and c[idx] > 0:
                        r_t = (c[idx + 1] - c[idx]) / c[idx]
                        contrib = live_weights.get(t, 0.0) * r_t
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

            # Change 1: apply exit transaction costs for positions still open at end of window
            exit_weight = sum(live_weights.get(t, 0.0) for t in valid)
            if exit_weight > 0:
                portfolio_val *= (1.0 - TC_PER_SIDE_PB * exit_weight)

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
            # Change 1: gate comparison showing mid-window exit impact and cost drag
            "gate_comparison": {
                "total_rebalances": (n_days - TRAIN) // TEST,
                "mid_window_exits": mid_window_exits,
                "transaction_cost_bps_per_side": int(TC_PER_SIDE_PB * 10000),
                "notes": "mid_window_exits fired when training-period HMM matrix flips to SELL",
            },
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ═══════════════════════════════════════════════════════════════════════════════
# AUTOMATION HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _macro_regime() -> tuple[bool, float]:
    """Returns (spy_above_200d_ma, vix_level)."""
    global _MACRO_CACHE
    # Change 5: lock prevents two threads from both finding the cache stale and double-fetching
    with _MACRO_LOCK:
        if _MACRO_CACHE is not None and (time.time() - _MACRO_CACHE[1]) < _MACRO_TTL:
            return _MACRO_CACHE[0]
    # Fix 4-A: fail CLOSED. If SPY/VIX data is unavailable we must not assume a
    # benign regime — treat SPY as below its 200d MA (bear → stricter threshold)
    # and VIX as elevated (blocks new buys) until the data comes back.
    spy_above = True
    spy_ok = False
    try:
        h = yf.Ticker("SPY").history(period="1y")
        if len(h) >= 200:
            spy_above = float(h["Close"].iloc[-1]) > float(h["Close"].iloc[-200:].mean())
            spy_ok = True
    except Exception as e:
        logger.warning("Macro: SPY fetch failed (%s)", e)
    if not spy_ok:
        spy_above = False  # fail closed → bear-level threshold
        logger.warning("Macro: SPY 200d MA unavailable — failing closed to bearish regime")

    vix = 20.0
    vix_ok = False
    try:
        h = yf.Ticker("^VIX").history(period="5d")
        if not h.empty:
            vix = float(h["Close"].iloc[-1])
            vix_ok = True
    except Exception as e:
        logger.warning("Macro: VIX fetch failed (%s)", e)
    if not vix_ok:
        vix = 999.0  # fail closed → high_vix gate blocks new buys
        logger.warning("Macro: VIX unavailable — failing closed to elevated VIX (new buys blocked)")

    result = (spy_above, vix)
    with _MACRO_LOCK:
        _MACRO_CACHE = (result, time.time())
    return result


def _earnings_within_days(ticker: str, days: int = 2) -> bool:
    """Return True if earnings announcement is within `days` calendar days."""
    # Change 5: lock prevents TOCTOU race when multiple threads check the same ticker
    with _EARNINGS_LOCK:
        cached = _EARNINGS_CACHE.get(ticker)
        if cached is not None and (time.time() - cached[1]) < _EARNINGS_TTL:
            return cached[0]
    result = False
    try:
        cal = yf.Ticker(ticker).calendar
        if cal is not None:
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
                        result = True
                        break
                except Exception:
                    pass
    except Exception:
        pass
    with _EARNINGS_LOCK:
        _EARNINGS_CACHE[ticker] = (result, time.time())
    return result


def _compute_kelly_params(trades: list[dict]) -> Optional[tuple[float, float, float]]:
    """Return (p, b, f_star) from a list of {return_pct} dicts, or None if insufficient data."""
    wins   = [t["return_pct"] for t in trades if t["return_pct"] > 0]
    losses = [abs(t["return_pct"]) for t in trades if t["return_pct"] <= 0]
    if not wins or not losses:
        return None
    p = len(wins) / len(trades)
    q = 1.0 - p
    b = (sum(wins) / len(wins)) / (sum(losses) / len(losses))
    f_star = max(0.0, (p * b - q) / b)
    return p, b, f_star


def _position_dollars(
    ticker: str, equity: float, score: float = 75.0, vol_21d: Optional[float] = None
) -> tuple[float, float, str]:
    """Returns (position_dollars, kelly_fraction, sizing_method).

    Uses fractional Kelly when ≥10 ticker trades exist, portfolio-wide prior when
    the portfolio has ≥20 trades but the ticker has <10, otherwise vol-targeting.
    Conviction and performance multipliers apply on top in all cases.
    """
    # ── Vol-target baseline (always computed; serves as fallback and 3× cap reference) ──
    if vol_21d is not None and vol_21d > 0:
        daily_vol = vol_21d
    else:
        try:
            df = fetch_ohlcv(ticker, days=30, min_bars=22)
            c = df["Close"].values.astype(float)
            rets_21 = np.diff(c[-22:]) / c[-22:-1]
            daily_vol = float(rets_21.std())
        except Exception:
            daily_vol = 0.0
    vol_weight = (0.01 / daily_vol) if daily_vol > 0 else 0.05

    # Conviction multiplier: score 75→1×, 85→1.25×, 95→1.5×
    multiplier = min(1.0 + max(0.0, score - 75.0) / 40.0, 1.5)

    # Performance multiplier
    perf = db.get_ticker_performance(ticker)
    perf_multiplier = 1.0
    if perf and perf["total_trades"] >= 3:
        if perf["win_rate"] > 0.6:
            perf_multiplier = 1.2
        elif perf["win_rate"] < 0.4:
            perf_multiplier = 0.7

    vol_base = vol_weight * equity * multiplier * perf_multiplier

    # ── Kelly sizing ──────────────────────────────────────────────────────────
    ticker_trades = db.get_trades_for_kelly(ticker)
    all_trades    = db.get_all_trades_for_kelly()

    kelly_frac: float = 0.0
    sizing_method = "vol_target_fallback"

    if len(ticker_trades) >= 10:
        params = _compute_kelly_params(ticker_trades)
        if params is not None:
            _, _, f_star = params
            kelly_frac = f_star * 0.5  # half-Kelly
            sizing_method = "kelly"
    elif len(all_trades) >= 20:
        params = _compute_kelly_params(all_trades)
        if params is not None:
            _, _, f_star = params
            kelly_frac = f_star * 0.5
            sizing_method = "kelly_portfolio_prior"

    if sizing_method != "vol_target_fallback":
        kelly_dollars = kelly_frac * equity * multiplier * perf_multiplier
        # If Kelly is more than 3× vol-target, warn and cap
        cap_3x = vol_base * 3.0
        if kelly_dollars > cap_3x:
            logger.warning(
                "%s: Kelly $%.0f exceeds 3× vol-target $%.0f — capping",
                ticker, kelly_dollars, cap_3x,
            )
            kelly_dollars = cap_3x
        # Hard cap 10%, floor 0.5%
        kelly_dollars = max(min(kelly_dollars, equity * 0.10), equity * 0.005)
        return kelly_dollars, round(kelly_frac, 6), sizing_method

    # Vol-targeting fallback
    raw_dollars = max(min(vol_base, equity * 0.10), equity * 0.005)
    return raw_dollars, 0.0, "vol_target_fallback"


def _trading_days_between(start: datetime, end: datetime) -> int:
    try:
        return int(np.busday_count(start.date(), end.date()))
    except Exception:
        return max(0, int((end - start).days * 5 / 7))


def _close_and_record(api, ticker: str, current_price: float, entry_price: float,
                      exit_reason: str, entry_log: Optional[dict],
                      score: Optional[float] = None) -> None:
    """Close an Alpaca position and write trade_outcomes."""
    api.close_position(ticker)
    ret = (current_price - entry_price) / entry_price * 100
    hold = 0
    if entry_log:
        try:
            hold = _trading_days_between(
                datetime.fromisoformat(entry_log["timestamp"]), datetime.utcnow()
            )
        except Exception:
            pass
    try:
        db.record_close_transaction(
            ticker, score, exit_reason, current_price, entry_price,
            ret, hold,
            entry_log["id"] if entry_log else None,
            entry_log.get("composite_score") if entry_log else None,
        )
    except Exception as db_err:
        logger.error(
            "%s: broker close SUCCEEDED (%s) but DB transaction FAILED — "
            "position not recorded, cooldown not set: %s",
            ticker, exit_reason, db_err,
        )
    logger.info("%s: closed (%s) at %.2f (%.1f%%)", ticker, exit_reason, current_price, ret)


# ── Scheduled jobs ────────────────────────────────────────────────────────────

def _write_gate_stats() -> None:
    cutoff = (datetime.utcnow() - timedelta(days=90)).isoformat()
    gate_names = [
        "hmm_not_buy_transition", "hmm_regime_uncertain", "score_below_threshold",
        "sentiment_too_low",
        "earnings_within_2d", "vix_too_high", "already_in_position",
        "volume_below_average",
        "overextended", "momentum_disagreement",
        "reentry_cooldown", "sector_concentration",
    ]
    total = db.count_buy_evaluations(cutoff)
    stats: dict[str, dict] = {}
    for gate in gate_names:
        rejected = db.count_gate_rejections(gate, cutoff)
        stats[gate] = {
            "evaluated": total,
            "rejected": rejected,
            # rejection_rate = pct_of_threshold_passers (legacy key); pct_of_evaluated uses true denominator
            "rejection_rate":   round(rejected / total, 4) if total > 0 else 0.0,
            "pct_of_evaluated": round(rejected / total, 4) if total > 0 else 0.0,
        }
    # Informational pass-counter (not a rejection): how often the Gaussian HMM probability
    # carried the HMM gate despite the transition matrix returning HOLD/SELL.
    gaussian_passes = db.count_signal_reason("hmm_passed_via_gaussian", cutoff)
    stats["hmm_passed_via_gaussian"] = {
        "evaluated": total,
        "passed": gaussian_passes,
        "pct_of_evaluated": round(gaussian_passes / total, 4) if total > 0 else 0.0,
    }
    with open(_GATE_STATS_PATH, "w") as f:
        json.dump(stats, f, indent=2)
    db.save_diagnostic("gate_stats", stats)
    logger.info("Gate stats written (%d total ticker-days evaluated, 90d)", total)


def _run_signal_job() -> None:
    job_start = time.time()
    logger.info("▶ Signal job starting")
    db.set_config("last_signal_job_at", datetime.utcnow().isoformat())
    api, err = _alpaca_client, _alpaca_err
    if api is None:
        logger.warning("Signal job aborted: %s", err)
        return

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
    high_vix = vix > 30
    bull_threshold = float(db.get_config("bull_threshold", "70"))
    bear_threshold = float(db.get_config("bear_threshold", "80"))
    buy_threshold  = bear_threshold if not spy_above else bull_threshold
    oe_thresh        = float(db.get_config("OVEREXTENDED_THRESHOLD_PCT", "0.25"))
    _mff_cfg         = db.get_config("MIN_FACTOR_FLOOR", "")
    min_factor_floor = float(_mff_cfg) if _mff_cfg else None
    logger.info("Macro: SPY>200d=%s VIX=%.1f threshold=%.0f",
                spy_above, vix, buy_threshold)

    # Sector counts of currently open positions
    open_sector_counts: dict[str, int] = {}
    for sym in positions:
        sec = _get_sector(sym)
        open_sector_counts[sec] = open_sector_counts.get(sec, 0) + 1

    # Pre-fetch sentiment sequentially with rate limiting before parallel factor computation.
    # The AV free tier allows ~5 req/min; 8 parallel workers would all fail simultaneously.
    # By pre-populating the cache here, the parallel workers get cache hits for sentiment.
    logger.info("Pre-fetching sentiment for %d tickers (13s gap each)...", len(watchlist))
    for _st in watchlist:
        with _SENTIMENT_LOCK:
            _cached, _ts = _SENTIMENT_CACHE.get(_st, ({}, 0.0))
            _cache_fresh = _cached and _cached.get("available") and (time.time() - _ts) < _SENTIMENT_TTL
        if not _cache_fresh:
            _get_sentiment_score(_st)
            time.sleep(_SENTIMENT_MIN_INTERVAL)

    # Compute factors for all watchlist tickers in parallel, then evaluate gates sequentially
    def _fetch_factors_safe(ticker: str) -> tuple[str, Optional[dict]]:
        try:
            return ticker, _compute_factors(ticker)
        except Exception as exc:
            logger.error("Factor compute failed for %s: %s", ticker, exc)
            return ticker, None

    with ThreadPoolExecutor(max_workers=min(len(watchlist), 8)) as pool:
        factor_results: dict[str, Optional[dict]] = dict(pool.map(_fetch_factors_safe, watchlist))

    # Sentinel: log warning if >50% of tickers returned None sentiment
    _with_factors = [r for r in factor_results.values() if r is not None]
    if _with_factors:
        _null_sent = sum(1 for r in _with_factors if r.get("sentiment_score") is None)
        _null_rate = _null_sent / len(_with_factors)
        if _null_rate > 0.5:
            logger.warning(
                "Sentiment degraded: %.0f%% null rate (%d/%d tickers)",
                _null_rate * 100, _null_sent, len(_with_factors),
            )
            db.save_diagnostic("sentiment_degraded", {
                "null_rate": round(_null_rate, 4),
                "null_count": _null_sent,
                "total": len(_with_factors),
                "timestamp": datetime.utcnow().isoformat(),
            })

    # Display-only: persist a cached snapshot for every ticker we computed, so the
    # homepage can render instantly without live computation. This is free (the data
    # is already in hand) and does not touch the trading logic / gates / thresholds below.
    for _t, _r in factor_results.items():
        if _r is not None:
            try:
                _write_snapshot(_t, _r)
            except Exception as e:
                logger.warning("snapshot write failed for %s: %s", _t, e)

    # Sequential gate evaluation and order submission (must not be parallelised)
    for ticker in watchlist:
        try:
            if _earnings_within_days(ticker):
                db.log_signal(ticker, None, None, "skipped", "earnings_within_2d", None, None)
                logger.info("%s: skipped (earnings soon)", ticker)
                continue

            result = factor_results.get(ticker)
            if result is None:
                db.log_signal(ticker, None, None, "skipped", "data_unavailable", None, None)
                continue

            composite          = result["composite_score"]
            hmm_signal         = result["hmm_signal"]
            hmm_regime         = result.get("hmm_regime")
            smoothed_bull_prob = result.get("smoothed_bull_prob", 0.5)
            hmm_fit_failed     = bool(result.get("hmm_fit_failed", False))
            sentiment          = result.get("sentiment_score")
            price              = result["current_price"]
            atr                = result.get("atr", 0.0)
            in_pos             = ticker in positions

            # MIN_FACTOR_FLOOR: cap entry score if any factor falls below the floor
            effective_composite = composite
            if min_factor_floor is not None:
                mfs = result.get("min_factor_score")
                if mfs is not None and mfs < min_factor_floor:
                    effective_composite = min(composite, buy_threshold - 5.0)

            # Score deterioration exit — skip for transition regime (smoothed_bull_prob in [0.35, 0.65])
            if in_pos and composite < 40.0 and hmm_regime != "transition":
                pos = positions[ticker]
                entry_log = db.get_last_buy_signal(ticker)
                try:
                    _close_and_record(api, ticker, price, float(pos.avg_entry_price),
                                      "score_deterioration", entry_log, score=composite)
                except Exception as e:
                    db.log_signal(ticker, composite, hmm_signal, "skipped",
                                  f"close_failed:{e}", price, atr)
                continue

            if hmm_signal == "SELL" and composite < 45.0 and in_pos:
                pos = positions[ticker]
                entry_log = db.get_last_buy_signal(ticker)
                try:
                    _close_and_record(api, ticker, price, float(pos.avg_entry_price),
                                      "sell_signal", entry_log, score=composite)
                except Exception as e:
                    db.log_signal(ticker, composite, "SELL", "skipped",
                                  f"close_failed:{e}", price, atr)
                continue

            # Combined HMM gate: pass if the transition-matrix signal is BUY OR the
            # Gaussian HMM is ≥70% confident in a bull regime. Neither model has sole veto.
            hmm_passes = (hmm_signal == "BUY") or (smoothed_bull_prob >= 0.70)
            if not hmm_passes:
                db.log_signal(ticker, composite, hmm_signal, "skipped", "hmm_not_buy_transition", price, atr,
                              hmm_regime=hmm_regime, sentiment_score=sentiment,
                              smoothed_bull_prob=smoothed_bull_prob, hmm_fit_failed=hmm_fit_failed)
                continue

            # Informational only (hmm_source: gaussian): the Gaussian probability — not the
            # transition matrix — carried the gate. Trade still proceeds; tracked in gate-stats.
            if hmm_signal != "BUY":
                db.log_signal(ticker, composite, hmm_signal, "evaluated",
                              "hmm_passed_via_gaussian", price, atr,
                              hmm_regime=hmm_regime, sentiment_score=sentiment,
                              smoothed_bull_prob=smoothed_bull_prob)

            # Kalman-smoothed regime gate: lowered to 0.55 since the combined gate above now
            # already requires ≥0.70 to pass on the Gaussian path alone (avoids 0.65–0.70 ambiguity).
            if smoothed_bull_prob <= 0.55:
                db.log_signal(ticker, composite, "BUY", "skipped",
                              f"hmm_regime_uncertain:{smoothed_bull_prob:.3f}",
                              price, atr, hmm_regime=hmm_regime, sentiment_score=sentiment,
                              smoothed_bull_prob=smoothed_bull_prob, hmm_fit_failed=hmm_fit_failed)
                continue

            if effective_composite < buy_threshold:
                db.log_signal(ticker, composite, hmm_signal, "skipped",
                              f"score_below_threshold:{effective_composite:.1f}<{buy_threshold:.0f}",
                              price, atr, hmm_regime=hmm_regime, sentiment_score=sentiment,
                              smoothed_bull_prob=smoothed_bull_prob)
                continue

            # Negative sentiment hard filter
            if sentiment is not None and sentiment < 35.0:
                db.log_signal(ticker, effective_composite, "BUY", "skipped",
                              f"sentiment_too_low:{sentiment:.1f}", price, atr,
                              hmm_regime=hmm_regime, sentiment_score=sentiment,
                              smoothed_bull_prob=smoothed_bull_prob)
                continue

            if high_vix:
                db.log_signal(ticker, effective_composite, "BUY", "skipped",
                              f"vix_too_high:{vix:.1f}", price, atr,
                              hmm_regime=hmm_regime, sentiment_score=sentiment,
                              smoothed_bull_prob=smoothed_bull_prob)
                continue
            if in_pos:
                db.log_signal(ticker, effective_composite, "BUY", "skipped",
                              "already_in_position", price, atr,
                              hmm_regime=hmm_regime, sentiment_score=sentiment,
                              smoothed_bull_prob=smoothed_bull_prob)
                continue
            # Fix 2-A: volume confirmation gate (previously computed but never applied)
            if not result.get("volume_ok", True):
                db.log_signal(ticker, effective_composite, "BUY", "skipped",
                              "volume_below_average", price, atr,
                              hmm_regime=hmm_regime, sentiment_score=sentiment,
                              smoothed_bull_prob=smoothed_bull_prob)
                continue
            price_ma20_ratio = result.get("price_ma20_ratio", 1.0)
            # Fix 2-B: use the true rolling-percentile momentum flag (matches backtest), not mom_score>=75
            top_quartile_mom = bool(result.get("top_quartile_mom", False))
            if not top_quartile_mom and price_ma20_ratio > (1.0 + oe_thresh):
                db.log_signal(ticker, effective_composite, "BUY", "skipped", "overextended", price, atr,
                              hmm_regime=hmm_regime, sentiment_score=sentiment,
                              smoothed_bull_prob=smoothed_bull_prob)
                continue
            ret_3m  = result.get("ret_3m")
            ret_12m = result.get("ret_12m")
            if ret_3m is not None and ret_12m is not None:
                if (ret_3m + ret_12m) <= 0 or ret_3m < -0.10 or ret_12m < -0.10:
                    db.log_signal(ticker, effective_composite, "BUY", "skipped",
                                  "momentum_disagreement", price, atr,
                                  hmm_regime=hmm_regime, sentiment_score=sentiment,
                                  smoothed_bull_prob=smoothed_bull_prob)
                    continue
            # Re-entry cooldown: block re-entry for 5 trading days after non-signal exits
            perf = db.get_ticker_performance(ticker)
            if perf and perf.get("last_exit_at"):
                try:
                    last_exit = datetime.fromisoformat(perf["last_exit_at"])
                    days_since = _trading_days_between(last_exit, datetime.utcnow())
                    if days_since < 5:
                        db.log_signal(ticker, effective_composite, "BUY", "skipped",
                                      "reentry_cooldown", price, atr,
                                      hmm_regime=hmm_regime, sentiment_score=sentiment,
                                      smoothed_bull_prob=smoothed_bull_prob)
                        continue
                except Exception:
                    pass
            sector = _get_sector(ticker)
            max_sector = int(db.get_config("MAX_SECTOR_POSITIONS", "3"))
            if sector != "Unknown" and open_sector_counts.get(sector, 0) >= max_sector:
                db.log_signal(ticker, effective_composite, "BUY", "skipped",
                              "sector_concentration", price, atr,
                              hmm_regime=hmm_regime, sentiment_score=sentiment,
                              smoothed_bull_prob=smoothed_bull_prob)
                continue

            dollars, kelly_frac, sizing_method = _position_dollars(
                ticker, equity, effective_composite, vol_21d=result.get("vol_21d")
            )
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
                signal_id = db.log_signal(
                    ticker, effective_composite, "BUY", "ordered", None, price, atr,
                    hmm_regime=hmm_regime, sentiment_score=sentiment,
                    smoothed_bull_prob=smoothed_bull_prob,
                    kelly_fraction=kelly_frac, sizing_method=sizing_method,
                    hmm_fit_failed=hmm_fit_failed,
                )
                if atr > 0:
                    db.update_trailing_stop(signal_id, price - 1.5 * atr)
                open_sector_counts[sector] = open_sector_counts.get(sector, 0) + 1
                logger.info(
                    "%s: BUY $%.0f score=%.1f regime=%s bull_prob=%.2f sizing=%s kelly=%.3f sector=%s",
                    ticker, dollars, effective_composite, hmm_regime,
                    smoothed_bull_prob, sizing_method, kelly_frac, sector,
                )
            except Exception as e:
                db.log_signal(ticker, composite, "BUY", "skipped",
                              f"order_failed:{e}", price, atr)
        except Exception as e:
            logger.error("Signal job error for %s: %s", ticker, e)

    # Change 5: log wall-clock time to confirm parallelisation speedup
    elapsed = round(time.time() - job_start, 2)
    logger.info("◀ Signal job done in %.1fs (watchlist=%d)", elapsed, len(watchlist))
    try:
        db.save_diagnostic("signal_job_timing", {
            "last_run_elapsed_sec": elapsed,
            "watchlist_size": len(watchlist),
            "timestamp": datetime.utcnow().isoformat(),
        })
        _write_gate_stats()
    except Exception as e:
        logger.warning("Failed to write gate stats / timing: %s", e)


def _run_stoploss_job() -> None:
    logger.info("▶ Stop-loss job starting")
    db.set_config("last_stoploss_job_at", datetime.utcnow().isoformat())
    api, err = _alpaca_client, _alpaca_err
    if api is None:
        logger.warning("Stop-loss job aborted: %s", err)
        return

    try:
        positions = api.get_all_positions()
    except Exception as e:
        logger.error("Stop-loss job: failed to list positions: %s", e)
        return

    # Macro drawdown protection: close all positions if SPY fell >3% over last 5 trading days
    try:
        spy_hist = yf.Ticker("SPY").history(period="10d")
        if len(spy_hist) >= 6:
            spy_5d_ret = (float(spy_hist["Close"].iloc[-1]) / float(spy_hist["Close"].iloc[-6]) - 1.0) * 100
            if spy_5d_ret < -3.0:
                logger.warning("SPY 5-day return %.2f%% — macro_drawdown_protection, closing all", spy_5d_ret)
                for pos in positions:
                    try:
                        _close_and_record(
                            api, pos.symbol,
                            float(pos.current_price), float(pos.avg_entry_price),
                            "macro_drawdown_protection",
                            db.get_last_buy_signal(pos.symbol),
                        )
                    except Exception as exc:
                        logger.error("macro_drawdown_protection close failed %s: %s", pos.symbol, exc)
                logger.info("◀ Stop-loss job done (macro_drawdown_protection)")
                return
    except Exception as exc:
        logger.warning("SPY 5-day check failed: %s", exc)

    now = datetime.utcnow()
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
            try:
                df_atr = fetch_ohlcv(ticker, days=90, min_bars=25)
                current_atr = _atr_from_df(df_atr)
            except Exception:
                current_atr = 0.0
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


def _run_adaptive_thresholds_job() -> None:
    """
    Change 4: Weekly job — adjust buy thresholds using last 50 trades per regime.
    Uses continuous EWA update (not discrete ±5 steps) to avoid threshold jumps.
    Bull and bear thresholds adapt independently using trades from matching regime.
    Old and new thresholds are logged to diagnostic_snapshots for audit.
    """
    logger.info("▶ Adaptive thresholds job starting")

    BULL_MIN, BULL_MAX = 65.0, 80.0
    BEAR_MIN, BEAR_MAX = 75.0, 85.0
    EWA_ALPHA = 0.15  # new = old * 0.85 + target * 0.15
    MIN_TRADES = 5

    bull_old = float(db.get_config("bull_threshold", "70"))
    bear_old = float(db.get_config("bear_threshold", "80"))

    def _target_threshold(win_rate: float, t_min: float, t_max: float) -> float:
        """Linear map: 40% win rate → t_max (tighten), 60% win rate → t_min (loosen)."""
        clipped = max(0.4, min(0.6, win_rate))
        return t_max - (clipped - 0.4) / 0.2 * (t_max - t_min)

    bull_trades = db.get_last_n_trades_by_regime(50, regime="bull")
    bear_trades = db.get_last_n_trades_by_regime(50, regime="bear")

    bull_new = bull_old
    bull_win_rate = None
    if len(bull_trades) >= MIN_TRADES:
        bull_win_rate = sum(1 for t in bull_trades if t["return_pct"] > 0) / len(bull_trades)
        bull_target = _target_threshold(bull_win_rate, BULL_MIN, BULL_MAX)
        bull_new = round(bull_old * (1.0 - EWA_ALPHA) + bull_target * EWA_ALPHA, 2)
        bull_new = max(BULL_MIN, min(BULL_MAX, bull_new))
        logger.info("Bull: win_rate=%.2f target=%.1f old=%.2f new=%.2f (n=%d)",
                    bull_win_rate, bull_target, bull_old, bull_new, len(bull_trades))
    else:
        logger.info("Bull: skipping — only %d trades (need %d)", len(bull_trades), MIN_TRADES)

    bear_new = bear_old
    bear_win_rate = None
    if len(bear_trades) >= MIN_TRADES:
        bear_win_rate = sum(1 for t in bear_trades if t["return_pct"] > 0) / len(bear_trades)
        bear_target = _target_threshold(bear_win_rate, BEAR_MIN, BEAR_MAX)
        bear_new = round(bear_old * (1.0 - EWA_ALPHA) + bear_target * EWA_ALPHA, 2)
        bear_new = max(BEAR_MIN, min(BEAR_MAX, bear_new))
        logger.info("Bear: win_rate=%.2f target=%.1f old=%.2f new=%.2f (n=%d)",
                    bear_win_rate, bear_target, bear_old, bear_new, len(bear_trades))
    else:
        logger.info("Bear: skipping — only %d trades (need %d)", len(bear_trades), MIN_TRADES)

    now = datetime.utcnow().isoformat()
    db.set_config("bull_threshold", str(bull_new))
    db.set_config("bear_threshold", str(bear_new))
    db.set_config("thresholds_last_updated", now)

    # Log audit trail so threshold trajectory can be inspected via /api/debug
    audit = {
        "timestamp":        now,
        "bull_old":         bull_old,    "bull_new":         bull_new,
        "bear_old":         bear_old,    "bear_new":         bear_new,
        "bull_trade_count": len(bull_trades),
        "bear_trade_count": len(bear_trades),
        "bull_win_rate":    round(bull_win_rate, 4) if bull_win_rate is not None else None,
        "bear_win_rate":    round(bear_win_rate, 4) if bear_win_rate is not None else None,
        "ewa_alpha":        EWA_ALPHA,
    }
    db.save_diagnostic("threshold_audit", audit)
    logger.info("◀ Adaptive thresholds done: bull %.2f→%.2f bear %.2f→%.2f",
                bull_old, bull_new, bear_old, bear_new)


# ═══════════════════════════════════════════════════════════════════════════════
# ANALYTICS API
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/api/analytics")
def get_analytics():
    stats = db.get_analytics_data()
    bull_threshold = float(db.get_config("bull_threshold", "70"))
    bear_threshold = float(db.get_config("bear_threshold", "80"))
    thresholds_updated = db.get_config("thresholds_last_updated", "") or None
    last_signal_job    = db.get_config("last_signal_job_at", "") or None
    last_stoploss_job  = db.get_config("last_stoploss_job_at", "") or None
    open_positions = 0
    if _alpaca_client:
        try:
            open_positions = len(_alpaca_client.get_all_positions())
        except Exception:
            pass
    return {
        "by_exit_reason":  stats["by_exit_reason"],
        "by_score_bucket": stats["by_score_bucket"],
        "by_ticker":       stats["by_ticker"],
        "adaptive_thresholds": {
            "bull":         bull_threshold,
            "bear":         bear_threshold,
            "last_updated": thresholds_updated,
        },
        "system_health": {
            "last_signal_job":    last_signal_job,
            "last_stoploss_job":  last_stoploss_job,
            "open_positions":     open_positions,
            "total_closed_trades": stats["total_closed_trades"],
        },
    }


# ═══════════════════════════════════════════════════════════════════════════════
# WATCHLIST API
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/api/watchlist")
def api_get_watchlist():
    return db.get_watchlist()


@app.get("/api/watchlist/snapshot")
def api_watchlist_snapshot():
    """Cached display data for every watchlist ticker in one fast DB read.

    No yfinance calls, no HMM fitting, no live computation — this is what the homepage
    loads on mount. Tickers without a snapshot yet return null fields (UI shows
    "Calculating…"). See POST /api/watchlist/{ticker}/refresh and /api/factors/{ticker}
    for the explicit live-recompute paths.
    """
    return {"snapshots": db.get_watchlist_snapshots()}


def _compute_and_store_snapshot(ticker: str, force: bool = False) -> Optional[dict]:
    """Live-compute a single ticker and persist its display snapshot. Returns the snapshot."""
    result = _compute_factors(ticker, force=force)
    if result is None:
        return None
    _write_snapshot(ticker, result)
    return db.get_snapshot(ticker)


@app.post("/api/watchlist/{ticker}", status_code=201)
def api_add_ticker(ticker: str):
    ticker = ticker.upper()
    db.add_ticker(ticker)
    # New-ticker edge case: kick off a one-time live compute in the background so the
    # card fills in within seconds instead of sitting empty until tomorrow's signal job.
    # The card shows "Calculating…" until the snapshot lands.
    def _seed():
        try:
            _compute_and_store_snapshot(ticker)
        except Exception as e:
            logger.warning("initial snapshot compute failed for %s: %s", ticker, e)
    threading.Thread(target=_seed, daemon=True).start()
    return {"ticker": ticker, "status": "added"}


@app.post("/api/watchlist/{ticker}/refresh")
def api_refresh_ticker(ticker: str):
    """Live-recompute a single ticker and update its snapshot (the "refresh" button)."""
    ticker = ticker.upper()
    snapshot = _compute_and_store_snapshot(ticker, force=True)
    if snapshot is None:
        raise HTTPException(status_code=502, detail=f"Failed to compute factors for '{ticker}'")
    return snapshot


@app.delete("/api/watchlist/{ticker}")
def api_remove_ticker(ticker: str):
    ticker = ticker.upper()
    db.remove_ticker(ticker)
    # Cancel any open Alpaca orders for this ticker
    if _alpaca_client:
        try:
            for order in _alpaca_client.get_orders(GetOrdersRequest(status=QueryOrderStatus.OPEN)):
                if order.symbol == ticker:
                    _alpaca_client.cancel_order_by_id(order.id)
        except Exception as e:
            logger.warning("Could not cancel orders for %s: %s", ticker, e)
    return {"ticker": ticker, "status": "removed"}


# ═══════════════════════════════════════════════════════════════════════════════
# PAPER TRADING API
# ═══════════════════════════════════════════════════════════════════════════════

def _alpaca_or_error(label: str):
    """Return the Alpaca client or raise HTTPException with a clear message."""
    if _alpaca_client is None:
        raise HTTPException(status_code=503, detail=f"{label}: {_alpaca_err}")
    return _alpaca_client


@app.get("/api/paper/account")
def api_paper_account():
    if _alpaca_client is None:
        return {"available": False, "error": _alpaca_err}
    try:
        acc = _alpaca_client.get_account()
        n_pos = len(_alpaca_client.get_all_positions())
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
    if _alpaca_client is None:
        return {"available": False, "error": _alpaca_err}
    try:
        positions = _alpaca_client.get_all_positions()
    except Exception as e:
        return {"available": False, "error": str(e)}

    result = []
    now = datetime.utcnow()
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


# Ordered entry gates, matching the real evaluation order inside _run_signal_job().
# (key → the skip_reason prefix logged when the ticker stops at that gate.)
_DECISION_GATES: list[tuple[str, str]] = [
    ("earnings_within_2d",      "Not within 2 days of earnings"),
    ("data_unavailable",        "Price & factor data available"),
    ("hmm_not_buy_transition",  "HMM regime supports buying"),
    ("hmm_regime_uncertain",    "Smoothed regime confidence sufficient"),
    ("score_below_threshold",   "Composite score meets threshold"),
    ("sentiment_too_low",       "Sentiment not strongly negative"),
    ("vix_too_high",            "Market volatility (VIX) acceptable"),
    ("already_in_position",     "Not already holding this ticker"),
    ("volume_below_average",    "Volume confirmation"),
    ("overextended",            "Not overextended vs 20-day average"),
    ("momentum_disagreement",   "Momentum agreement (3m & 12m)"),
    ("reentry_cooldown",        "Re-entry cooldown cleared"),
    ("sector_concentration",    "Sector concentration within cap"),
]


def _pct(x: Optional[float]) -> str:
    return f"{x * 100:.0f}%" if x is not None else "—"


def _decision_trail_detail(key: str, status: str, anchor: dict, ctx: dict) -> str:
    """Plain-English detail string for one gate, built from the logged row values.

    Read-only formatting of data already persisted in signal_log — no computation.
    """
    regime = anchor.get("hmm_regime")
    bull_prob = anchor.get("smoothed_bull_prob")
    score = anchor.get("composite_score")
    reason = anchor.get("skip_reason") or ""

    if key == "earnings_within_2d":
        return ("Earnings within 2 trading days — entries paused"
                if status == "failed" else "No earnings reported in the next 2 trading days")
    if key == "data_unavailable":
        return ("Insufficient price/factor data to evaluate"
                if status == "failed" else "Sufficient price history and factors computed")
    if key == "hmm_not_buy_transition":
        regime_txt = regime or "unknown"
        path = "Gaussian probability" if ctx.get("gaussian_pass") else "transition matrix"
        if status == "failed":
            return f"Regime {regime_txt} — neither the transition matrix nor Gaussian HMM (bull prob {_pct(bull_prob)}) confirmed a bull"
        return f"Regime {regime_txt}, passed via {path} (bull prob {_pct(bull_prob)})"
    if key == "hmm_regime_uncertain":
        if status == "failed" and ":" in reason:
            val = reason.split(":", 1)[1]
            return f"Smoothed bull probability {val} below the 0.55 floor"
        return f"Smoothed bull probability {_pct(bull_prob)} (need > 55%)"
    if key == "score_below_threshold":
        if status == "failed" and ":" in reason:
            return f"Composite {reason.split(':', 1)[1]} (below threshold)"
        thr = ctx.get("threshold")
        score_txt = f"{score:.1f}" if score is not None else "—"
        return f"Composite score {score_txt} ≥ threshold {thr:.0f}" if thr is not None else f"Composite score {score_txt}"
    if key == "sentiment_too_low":
        sent = anchor.get("sentiment_score")
        if status == "failed" and ":" in reason:
            return f"Sentiment {reason.split(':', 1)[1]} (below 35 floor)"
        return f"Sentiment {sent:.0f} ≥ 35" if sent is not None else "Sentiment not strongly negative (or unavailable)"
    if key == "vix_too_high":
        if status == "failed" and ":" in reason:
            return f"VIX {reason.split(':', 1)[1]} — too high to enter"
        return "Market volatility (VIX) within acceptable range"
    if key == "already_in_position":
        return ("Already holding this ticker — no new entry"
                if status == "failed" else "Not currently held")
    if key == "volume_below_average":
        return ("Volume below the 20-day average — not confirmed"
                if status == "failed" else "Volume confirmed above the 20-day average")
    if key == "overextended":
        return ("Price extended too far above its 20-day average"
                if status == "failed" else "Price not overextended vs the 20-day average")
    if key == "momentum_disagreement":
        return ("3-month and 12-month momentum disagree or are negative"
                if status == "failed" else "3-month and 12-month momentum agree")
    if key == "reentry_cooldown":
        return ("Within the 5-day cooldown after a recent non-signal exit"
                if status == "failed" else "Outside the re-entry cooldown window")
    if key == "sector_concentration":
        return ("Sector already at the open-position cap"
                if status == "failed" else "Sector concentration within the cap")
    return ""


def _build_decision_trail(ticker: str) -> dict:
    rows = db.get_recent_signal_rows_for_ticker(ticker, limit=25)
    if not rows:
        return {
            "ticker": ticker, "evaluated": False, "evaluated_at": None,
            "outcome": "no_data", "would_trade_today": False,
            "summary": "This ticker has not been evaluated by the signal job yet.",
            "gates": [], "order": None,
        }

    # Anchor on the most recent *entry-evaluation* row (ignore stop-loss 'closed' exits,
    # which are position management, not a buy decision).
    entry_rows = [r for r in rows if r.get("action") != "closed"]
    if not entry_rows:
        return {
            "ticker": ticker, "evaluated": False, "evaluated_at": rows[0].get("timestamp"),
            "outcome": "exit_only", "would_trade_today": False,
            "summary": "The most recent activity was a position exit, not a buy evaluation.",
            "gates": [], "order": None,
        }

    anchor = entry_rows[0]
    anchor_ts = anchor.get("timestamp") or ""
    # Rows from the same run = same ticker within ~10 min of the anchor.
    run_rows = [anchor]
    try:
        anchor_dt = datetime.fromisoformat(anchor_ts)
        for r in entry_rows[1:]:
            try:
                dt = datetime.fromisoformat(r.get("timestamp") or "")
            except Exception:
                continue
            if abs((anchor_dt - dt).total_seconds()) <= 600:
                run_rows.append(r)
    except Exception:
        pass

    # Informational marker logged when the Gaussian probability (not the matrix) carried HMM.
    gaussian_pass = any((r.get("skip_reason") or "").startswith("hmm_passed_via_gaussian") for r in run_rows)

    ctx = {
        "gaussian_pass": gaussian_pass,
        "threshold": None,
    }
    try:
        # Display context only: the thresholds the gate uses (read from config, not recomputed).
        bull_thr = float(db.get_config("bull_threshold", "70"))
        bear_thr = float(db.get_config("bear_threshold", "80"))
        # Without re-running the macro check we can't know which applied; show the lower
        # (bull) threshold as the optimistic bar a passing score cleared.
        ctx["threshold"] = bull_thr
        ctx["bull_threshold"] = bull_thr
        ctx["bear_threshold"] = bear_thr
    except Exception:
        pass

    action = anchor.get("action")
    reason = anchor.get("skip_reason") or ""

    # Find which gate the run stopped at.
    stop_index: Optional[int] = None
    if action == "ordered":
        stop_index = None  # passed everything
    else:
        for i, (key, _label) in enumerate(_DECISION_GATES):
            if reason.startswith(key):
                stop_index = i
                break

    gates: list[dict] = []
    ordered = action == "ordered"

    if stop_index is None and not ordered:
        # Unrecognised outcome (e.g. score_deterioration / order_failed). Report it plainly
        # without fabricating gate results we can't verify from the log.
        return {
            "ticker": ticker, "evaluated": True, "evaluated_at": anchor_ts,
            "outcome": "other", "would_trade_today": False,
            "summary": f"Most recent outcome: {reason or action}.",
            "gates": [], "order": None,
        }

    last = len(_DECISION_GATES) - 1 if ordered else stop_index
    for i, (key, label) in enumerate(_DECISION_GATES):
        if i > last:
            break
        status = "failed" if (not ordered and i == stop_index) else "passed"
        gates.append({
            "key": key, "label": label, "status": status,
            "detail": _decision_trail_detail(key, status, anchor, ctx),
        })

    order = None
    summary = ""
    would_trade = False
    if ordered:
        price = anchor.get("price_at_signal")
        order = {
            "price": price,
            "kelly_fraction": anchor.get("kelly_fraction"),
            "sizing_method": anchor.get("sizing_method"),
        }
        gates.append({
            "key": "ordered", "label": "Order placed", "status": "ordered",
            "detail": (f"Buy order submitted at ${price:.2f}"
                       + (f" · sizing: {anchor.get('sizing_method')}" if anchor.get("sizing_method") else "")
                       if price is not None else "Buy order submitted"),
        })
        would_trade = True
        summary = "All gates passed — an order was placed."
    else:
        failed_label = _DECISION_GATES[stop_index][1]
        failed_detail = gates[-1]["detail"] if gates else ""
        if reason.startswith("already_in_position"):
            summary = "Already holding this ticker — no new entry today."
        else:
            summary = failed_detail or f"Stopped at: {failed_label}."

    return {
        "ticker": ticker,
        "evaluated": True,
        "evaluated_at": anchor_ts,
        "outcome": "ordered" if ordered else "skipped",
        "would_trade_today": would_trade,
        "summary": summary,
        "gates": gates,
        "order": order,
    }


@app.get("/api/decision-trail/{ticker}")
def api_decision_trail(ticker: str):
    """Read-only reconstruction of the most recent gate-by-gate evaluation for a ticker.

    Reads the latest signal_log rows (already written by _run_signal_job) and formats
    them as an ordered pass/fail trail. No scoring, gating or trading logic runs here.
    """
    return _build_decision_trail(ticker.upper())


@app.post("/api/paper/run-now")
def api_run_now():
    """Trigger the signal job immediately in a background thread."""
    t = threading.Thread(target=_run_signal_job, daemon=True)
    t.start()
    return {"status": "started", "message": "Signal job running in background — check /api/signals/log for results"}
