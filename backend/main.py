"""Stock Signal Tracker v2 — HMM regimes, multi-factor composite, walk-forward backtest."""
from __future__ import annotations
import json
import math
import re
import warnings
import time
import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from xml.etree import ElementTree as ET
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import yfinance as yf
from yfinance.exceptions import YFTzMissingError, YFRateLimitError, YFTickerMissingError
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
    from alpaca.trading.requests import (
        MarketOrderRequest, GetOrdersRequest, GetPortfolioHistoryRequest,
        # Aliased: main.py defines its own ClosePositionRequest (FastAPI body model) below,
        # which would otherwise shadow this SDK type and break the partial-close call.
        ClosePositionRequest as AlpacaClosePositionRequest,
    )
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

# Initialize schema + run migrations unconditionally at process startup, at
# import time — before any endpoint is registered/reachable, before the
# scheduler starts, and before any DB read/write can occur. This previously
# ran only inside the @app.on_event("startup") lifespan hook, which is not a
# guaranteed per-process-launch hook (it only fires when the ASGI lifespan
# protocol is driven, and not when this module is imported by a script, a
# background job, or a test), so migrations could silently never apply.
# database.DB_PATH is already resolved (after load_dotenv above), so this runs
# against the exact same file every other DB operation uses at runtime.
db.init_db()

# Auth wiring. Imported after load_dotenv() so JWT_SECRET is available (auth.py
# raises at import if it's missing) and after init_db() so the users / token
# tables exist.
import auth
from routers import auth_router

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

# require_auth is applied app-wide: every request must carry a valid access-token
# cookie except CORS preflight, GET /health, and the /auth/* routes (the gate
# exempts those by path). This is the router-level-equivalent enforcement for a
# codebase whose 50 endpoints all hang off this single `app`, not off APIRouters.
app = FastAPI(
    title="Stock Signal Tracker v2",
    dependencies=[Depends(auth.require_auth)],
)

# Cookie auth is cross-site (frontend on Vercel, API on Railway), so CORS must
# echo the exact origin and allow credentials — a "*" origin is forbidden once
# allow_credentials=True. FRONTEND_URL is the deployed app; CORS_ORIGINS can add
# more; the regex keeps every localhost port working in dev.
_cors_origins_env = os.getenv("CORS_ORIGINS", "")
_extra_origins = [o.strip() for o in _cors_origins_env.split(",") if o.strip()]
_frontend_url = os.getenv("FRONTEND_URL", "").rstrip("/")
if _frontend_url:
    _extra_origins.append(_frontend_url)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_extra_origins,
    allow_origin_regex=r"http://localhost:\d+",
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["*"],
)

app.include_router(auth_router.router)


@app.on_event("startup")
def _startup() -> None:
    # Schema init + migrations now run unconditionally at import time (see the
    # db.init_db() call near the top of this module). The scheduler is started
    # here, after the DB is guaranteed migrated.
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
        UTC = ZoneInfo("UTC")
        # Sample account equity every 5 min during (extended) market hours so the 1D curve
        # is populated even with no browser open. 13:30–21:00 UTC ≈ the US regular session.
        _scheduler.add_job(
            _run_equity_sampler_job,
            CronTrigger(day_of_week="mon-fri", hour="13-20", minute="*/5", timezone=UTC),
            id="equity_sampler_job", replace_existing=True,
        )
        _scheduler.add_job(
            _run_equity_prune_job,
            CronTrigger(hour=0, minute=0, timezone=UTC),
            id="equity_prune_job", replace_existing=True,
        )
        _scheduler.start()
        logger.info("Scheduler started (signal@15:30 ET, stop-loss@09:35 ET, thresholds@Sun18:00 ET, "
                    "equity-sampler@*/5min 13-20 UTC, equity-prune@00:00 UTC)")
    else:
        logger.warning("APScheduler not available — scheduled jobs disabled")

    # Cache the account creation date once (start of the 1Y / Max equity curves).
    _get_account_created_at()


@app.on_event("shutdown")
def _shutdown() -> None:
    if SCHEDULER_OK and _scheduler is not None and _scheduler.running:
        _scheduler.shutdown(wait=False)
    try:
        _http_client.close()
    except Exception:
        pass

# ── Constants ─────────────────────────────────────────────────────────────────

_SENTIMENT_CACHE: dict[str, tuple[dict, float]] = {}  # ticker → (result, timestamp)
_SENTIMENT_TTL = 900  # 15 minutes
_SENTIMENT_LOCK = threading.Lock()
_SENTIMENT_LAST_CALL = 0.0
_SENTIMENT_MIN_INTERVAL = 13.0  # ~4.5 req/min, safely under the 5/min AV free-tier limit
_SECTOR_CACHE: dict[str, tuple[str, float]] = {}  # ticker → (sector, timestamp)
_SECTOR_TTL = 86400  # 24 hours

# Company info cache — name/sector/industry/summary + a few trader fields and the
# last ~4 quarters of earnings. This data essentially never changes intraday (and
# changes only quarterly), so cache it for 7 days, far longer than the 24h sector cache.
_COMPANY_CACHE: dict[str, tuple[dict, float]] = {}  # ticker → (info dict, timestamp)
_COMPANY_TTL = 7 * 86400  # 7 days
_COMPANY_LOCK = threading.Lock()
# Retry config for the .info fetch behind _get_company_info. Yahoo throttles the
# 8-worker signal-job burst by returning a partial/empty .info dict with no exception;
# we retry it (like _fetch_sector) and never cache a name-less result, so a rate-limited
# burst can't freeze a ticker's name at None for the full 7-day TTL.
_COMPANY_FETCH_ATTEMPTS = 3
_COMPANY_FETCH_DELAY = 2.0  # seconds between attempts

# Full-history price cache for the chart's "Max" range. A period="max" fetch can span
# 20+ years (~270 KB for NVDA) and is user-triggered, so cache the projected points for
# 1 h to avoid re-pulling the whole series on every revisit. Independent of the 760-day
# signal/factor window — see fetch_ohlcv_max / get_price_history.
_PRICE_HISTORY_CACHE: dict[str, tuple[list, float]] = {}  # ticker → (points, timestamp)
_PRICE_HISTORY_TTL = 3600  # 1 hour
_PRICE_HISTORY_LOCK = threading.Lock()

# Portfolio equity-curve cache for the /portfolio page. Keyed by period string alone
# (timeframe is now fixed per period, so there's no collision risk). TTL varies by period
# (see _history_ttl): 1D every minute, 1W every 5 min, longer ranges every 10 min.
_PORTFOLIO_HISTORY_CACHE: dict[str, tuple[dict, float]] = {}  # period → (result, timestamp)

# Selector periods the /api/portfolio/history endpoint accepts. 1D is a rolling 24h window
# from Alpaca (1Min bars, extended hours), with equity_snapshots as fallback; the rest map
# to an Alpaca portfolio-history request at 1D bars.
_HISTORY_PERIODS = {"1D", "1W", "1M", "3M", "YTD", "1Y", "Max"}

# Account creation date (start of the 1Y / Max curves), fetched from Alpaca once per
# process and cached here — it never changes. Stored as naive UTC so it can both seed
# Alpaca requests and be compared against utcfromtimestamp() points without tz mismatch.
_ACCOUNT_CREATED_AT: Optional[datetime] = None


def _get_account_created_at() -> Optional[datetime]:
    """Account creation timestamp (naive UTC), the start of the 1Y and Max curves.

    Fetched from Alpaca once and cached for the process lifetime. Called eagerly on
    startup and lazily on first use as a fallback if startup fetch failed.
    """
    global _ACCOUNT_CREATED_AT
    if _ACCOUNT_CREATED_AT is None and _alpaca_client is not None:
        try:
            created = _alpaca_client.get_account().created_at
            if created is not None and created.tzinfo is not None:
                created = created.astimezone(timezone.utc).replace(tzinfo=None)
            _ACCOUNT_CREATED_AT = created
        except Exception:
            pass
    return _ACCOUNT_CREATED_AT


def _history_ttl(period: str) -> int:
    """Cache TTL (seconds) for an equity-curve period: 1 min for the live 1D view,
    5 min for 1W, 10 min for everything longer."""
    if period == "1D":
        return 60
    if period == "1W":
        return 300
    return 600

# Aggregate-expectancy cache for the /portfolio Edge Statistics section. Read-only
# aggregation over closed trades; 10-min TTL since it changes only when a trade exits.
_EDGE_STATS_CACHE: Optional[tuple[dict, float]] = None  # (result, timestamp)
_EDGE_STATS_TTL = 600  # 10 minutes

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

# _fetch_insider result cache. _fetch_insider is hit at least twice per ticker per
# signal cycle (once from /api/insider, once from factor computation); a 1-hour TTL
# collapses those into a single EDGAR round-trip. Failures are not cached (see
# _fetch_insider) so they retry on the next call. Lock guards parallel factor workers.
_INSIDER_CACHE: dict[str, tuple[datetime, Any]] = {}  # ticker → (utc_timestamp, result)
_INSIDER_TTL = timedelta(hours=1)
_INSIDER_LOCK = threading.Lock()

# ── Factor weight configuration ───────────────────────────────────────────────

# Change 2: sentiment added at 12%; earnings reduced 25→18%, insider 10→5% to keep total=100%
# Jun 2026: discrete-Markov factor removed. The five surviving factors kept their relative
# proportions and were renormalised (each divided by the pre-removal non-Markov sum 0.9296)
# so the weights again sum to exactly 1.0 with the same momentum/trend tilt as before.
DEFAULT_FACTOR_WEIGHTS: dict[str, float] = {
    "momentum":  0.33132530,
    "vol_trend": 0.26506024,
    "earnings":  0.20826162,
    "insider":   0.06626506,
    "sentiment": 0.12908778,
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

# Upcoming-earnings cache (1h TTL) for the Strategy Lab earnings table. Stores per-ticker
# {next earnings date + last two EPS surprises} or None when no calendar data is available.
_UPCOMING_EARNINGS_LOCK = threading.Lock()
_UPCOMING_EARNINGS_CACHE: dict[str, tuple[Optional[dict], float]] = {}
_UPCOMING_EARNINGS_TTL = 3600  # 1 hour

# Macro regime cache (5-min TTL — SPY/VIX checked once per signal job, not per ticker)
_MACRO_CACHE: Optional[tuple] = None  # (detail_dict, timestamp); see _macro_regime_detail()
_MACRO_TTL = 300  # seconds

# ── Feature preparation ───────────────────────────────────────────────────────

def extract_features(df: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Returns (closes, returns, vol_aligned, vol_20d_aligned) — all len(df)-1 except closes."""
    closes  = df["Close"].values.astype(float)
    volumes = df["Volume"].values.astype(float)
    returns = np.diff(closes) / closes[:-1]
    vol_20d = pd.Series(volumes).rolling(20, min_periods=1).mean().values
    # returns[i] corresponds to close[i+1]; align volume/20d to that day
    return closes, returns, volumes[1:], vol_20d[1:]

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

def fetch_ohlcv_max(ticker: str, min_bars: int = 2, max_retries: int = 3) -> pd.DataFrame:
    """Fetch the full available daily history for charting (yfinance period="max").

    Deliberately separate from fetch_ohlcv so the chart's price-history window is
    decoupled from the 760-day window used by _compute_factors / the signal job — neither
    constrains the other. Uses the same auto_adjust=True as fetch_ohlcv, so split/dividend
    adjustment is consistent across both paths (no artificial cliff at split dates)."""
    last_exc = None
    for attempt in range(max_retries):
        try:
            df = yf.Ticker(ticker).history(period="max", auto_adjust=True, raise_errors=True)
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


def fetch_ohlcv_window(
    ticker: str,
    interval: str = "1d",
    yf_period: Optional[str] = None,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    prepost: bool = False,
    min_bars: int = 2,
    max_retries: int = 3,
) -> pd.DataFrame:
    """Fetch OHLCV at a given interval/window for the stock-chart price-history endpoint.

    Used by the period-scoped /api/price-history path: intraday (1m for 1D, 15m for 1W) and
    daily (1M/3M/YTD/1Y). yfinance only serves intraday bars for recent windows (1m ≤ 30d,
    15m ≤ 60d), which is fine since 1D/1W are recent by definition. Separate from fetch_ohlcv
    (the 760-day signal window) and fetch_ohlcv_max (full daily history) so none constrains
    the others. Pass either `start`/`end` (e.g. 1D's rolling 24h window, or YTD's Jan 1) or
    `yf_period` (e.g. "1mo"). `prepost=True` includes pre-market/after-hours bars (intraday)."""
    last_exc = None
    for attempt in range(max_retries):
        try:
            kwargs = dict(interval=interval, auto_adjust=True, raise_errors=True,
                          prepost=prepost)
            if start is not None:
                kwargs["start"] = start
                if end is not None:
                    kwargs["end"] = end
            else:
                kwargs["period"] = yf_period
            df = yf.Ticker(ticker).history(**kwargs)
            if df.empty or len(df) < min_bars:
                raise HTTPException(status_code=404, detail=f"Insufficient data for '{ticker}'")
            # Drop NaN rows and any zero-close bars (yfinance occasionally emits empty
            # pre/post-market placeholder rows with Close == 0).
            df = df[["High", "Low", "Close", "Volume"]].dropna()
            df = df[df["Close"] > 0]
            if len(df) < min_bars:
                raise HTTPException(status_code=404, detail=f"Insufficient data for '{ticker}'")
            return df
        except HTTPException:
            raise
        except Exception as exc:
            last_exc = exc
            if "Quote not found" in str(exc) or isinstance(exc, YFTickerMissingError):
                raise HTTPException(status_code=404, detail=f"'{ticker}' not found")
            if _transient(exc):
                time.sleep(1.5 * (attempt + 1))
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

# ── /api/price-history ────────────────────────────────────────────────────────

def _project_points(df: pd.DataFrame, intraday: bool = False) -> list:
    """Project an OHLCV frame to the [{date, close, volume}] shape the chart consumes.

    For daily bars `date` is "YYYY-MM-DD"; for intraday bars (1D/1W) it's a full ISO
    timestamp (tz-aware, e.g. "2026-06-20T09:30:00-04:00") so the chart can render
    DD/MM HH:MM on the axis and tooltip."""
    idx = df.index
    closes = df["Close"].values
    vols = df["Volume"].values
    return [
        {
            "date": idx[i].isoformat() if intraday else idx[i].strftime("%Y-%m-%d"),
            "close": round(float(closes[i]), 4),
            "volume": int(vols[i]) if not np.isnan(vols[i]) else 0,
        }
        for i in range(len(df))
    ]


# Stock-chart intraday periods → yfinance interval + window (1D/1W only). Both fetch
# pre/post-market bars (prepost). 1D uses a rolling 24h start/end window (not yfinance
# period="1d", which only covers the regular 9:30–16:00 ET session) so the chart matches
# the portfolio equity curve: pre-market from ~04:00 ET and after-hours to ~20:00 ET.
_CHART_INTRADAY_SPEC = {
    "1D": {"interval": "1m",  "window_hours": 24, "prepost": True},
    "1W": {"interval": "15m", "yf_period": "5d",  "prepost": True},
}
# Daily-interval periods → calendar-day lookback for the *visible* window. 1Y uses 366 to
# cover leap years; YTD is handled separately (visible window starts at Jan 1).
_CHART_DAILY_DAYS = {"1M": 31, "3M": 92, "1Y": 366}
# Extra history fetched *before* a daily window's visible start so MA50/MA200 are populated
# from the very first visible bar. 200 trading days ≈ 290 calendar days (weekends/holidays);
# this lead-in is returned in `points` but trimmed from the chart via `visible_from`.
_MA_LEAD_IN_DAYS = 290


@app.get("/api/price-history/{ticker}")
def get_price_history(ticker: str, days: int = 760, period: Optional[str] = None):
    """Historical closing price + volume for charting.

    Read-only and independent of the signal path: it makes its own yfinance fetch and does
    NOT share the 760-day window used by _compute_factors / the signal job. No scores, signals
    or MAs are computed here (moving-average overlays are drawn client-side from these closes).

    `period` (the stock-chart selector) scopes the fetch server-side:
      1D  → 1-minute bars, last 1d      1M  → daily bars, last ~1mo
      1W  → 15-minute bars, last 5d     3M  → daily bars, last ~3mo
      YTD → daily bars from Jan 1       1Y  → daily bars, last ~1y
      Max → full daily history (yfinance period="max", cached 1 h since it's heavy)
    For the daily periods (1M/3M/YTD/1Y) an extra ~200-trading-day lead-in is fetched before
    the visible window and returned in `points`, with `visible_from` (ISO YYYY-MM-DD) marking
    where the chart should start drawing — so MA50/MA200 are valid from the first visible bar
    while the lead-in stays off-axis. Without `period` (e.g. the analytics buy & hold), a
    `days`-bar daily window is fetched (default 760, capped at 1100).
    """
    ticker = ticker.upper()
    if period:
        if period.lower() == "max":
            with _PRICE_HISTORY_LOCK:
                cached, ts = _PRICE_HISTORY_CACHE.get(ticker, (None, 0.0))
                if cached is not None and (time.time() - ts) < _PRICE_HISTORY_TTL:
                    return {"ticker": ticker, "period": "max", "intraday": False, "points": cached}
            points = _project_points(fetch_ohlcv_max(ticker, min_bars=2))
            with _PRICE_HISTORY_LOCK:
                _PRICE_HISTORY_CACHE[ticker] = (points, time.time())
            return {"ticker": ticker, "period": "max", "intraday": False, "points": points}

        spec = _CHART_INTRADAY_SPEC.get(period)
        if spec:
            if spec.get("window_hours"):
                end = datetime.now(timezone.utc)
                start = end - timedelta(hours=spec["window_hours"])
                df = fetch_ohlcv_window(ticker, interval=spec["interval"],
                                        start=start, end=end,
                                        prepost=spec.get("prepost", False), min_bars=2)
            else:
                df = fetch_ohlcv_window(ticker, interval=spec["interval"],
                                        yf_period=spec["yf_period"],
                                        prepost=spec.get("prepost", False), min_bars=2)
            return {"ticker": ticker, "period": period, "intraday": True,
                    "points": _project_points(df, intraday=True)}

        # Daily periods (1M/3M/YTD/1Y): fetch the visible window plus an MA lead-in, then tell
        # the frontend where the visible window begins so it can trim the lead-in off the axis.
        visible_start = None
        if period == "YTD":
            visible_start = datetime(datetime.now().year, 1, 1)
        elif period in _CHART_DAILY_DAYS:
            visible_start = datetime.now() - timedelta(days=_CHART_DAILY_DAYS[period])
        if visible_start is not None:
            fetch_start = visible_start - timedelta(days=_MA_LEAD_IN_DAYS)
            df = fetch_ohlcv_window(ticker, interval="1d", start=fetch_start, min_bars=2)
            return {"ticker": ticker, "period": period, "intraday": False,
                    "visible_from": visible_start.strftime("%Y-%m-%d"),
                    "points": _project_points(df)}
        # Unknown period → fall through to the legacy days-window path below.

    days = max(10, min(int(days), 1100))
    df = fetch_ohlcv(ticker, days=days, min_bars=2)
    return {"ticker": ticker, "days": days, "intraday": False, "points": _project_points(df)}

# ── /api/signal ───────────────────────────────────────────────────────────────

# NOTE: This endpoint has no frontend caller — it is kept for manual debugging /
# inspection of a ticker's regime label only. Do not remove.
@app.get("/api/signal/{ticker}")
def get_signal(ticker: str):
    """Price quote + the Gaussian-HMM bull/bear regime label for a ticker.

    The discrete Markov chain that previously drove this endpoint was removed; the
    composite multi-factor score (served by /api/factors) is now the system's signal.
    """
    ticker = ticker.upper()

    # 2 years of OHLCV
    df = fetch_ohlcv(ticker, days=760, min_bars=260)
    closes, returns, _vol, _vol_20d = extract_features(df)
    hmm_returns = _normalize_returns_for_hmm(returns)

    # Fit the Gaussian HMM on the full 2-year return series; hard-label the latest bar.
    try:
        regime_seq, bull_id, _ = fit_regimes(hmm_returns)
        current_regime_id = int(regime_seq[-1])
        regime_label = "bull" if current_regime_id == bull_id else "bear"
    except Exception as e:
        logger.warning("HMM failed for %s: %s — using single regime", ticker, e)
        regime_label = "bull"

    cur_price  = float(closes[-1])
    prev_price = float(closes[-2])

    return {
        "ticker":      ticker,
        "price":       round(cur_price, 4),
        "prev_close":  round(prev_price, 4),
        "change_pct":  round((cur_price - prev_price) / prev_price * 100, 4),
        "regime":      regime_label,
    }

# ── /api/backtest ─────────────────────────────────────────────────────────────

@app.get("/api/backtest/{ticker}")
def get_backtest(ticker: str):
    ticker = ticker.upper()

    df = fetch_ohlcv(ticker, days=760, min_bars=260)
    closes, returns, _vol, _vol_20d = extract_features(df)
    dates = df.index.tolist()[1:]
    n = len(returns)  # = len(closes) - 1

    TRAIN = int(db.get_config("BACKTEST_TRAIN", "252"))
    TEST  = int(db.get_config("BACKTEST_TEST", "21"))
    # Fix 2-E: align with live system — 21-day max hold and ATR trailing stop on by default
    HOLD  = int(db.get_config("BACKTEST_HOLD", "21"))
    atr_mult     = float(db.get_config("BACKTEST_ATR_MULTIPLIER", "1.5"))
    use_atr_stop = db.get_config("BACKTEST_ATR_STOP", "true").lower() == "true"
    macro_filter = db.get_config("BACKTEST_MACRO_FILTER", "true").lower() == "true"
    # Entry now mirrors the live trader: enter when the composite score clears the same
    # bull-regime BUY threshold (default 63), not on a discrete-Markov BUY. The macro
    # filter below only lets trades through when SPY is above its 200-day MA (a bull
    # regime), so the bull threshold — not the higher bear threshold — is the right bar.
    buy_threshold = float(db.get_config("bull_threshold", "63"))
    # Mid-window deterioration exit, mirroring the live score-deterioration close.
    exit_threshold = 40.0

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

    # Change 1: precompute VIX history aligned to ticker dates for the VIX>30 gate.
    # Align by calendar date: the ^VIX feed is tz-aware in a different zone (CT) than
    # df.index (ET), so a direct timestamp reindex misses on every row and leaves the
    # whole series NaN. Any date still missing (or a failed fetch) is treated as
    # fail-open by the gate test in the loop below.
    vix_series_bt: Optional[np.ndarray] = None
    try:
        vix_df_bt = yf.Ticker("^VIX").history(period="2y")
        if not vix_df_bt.empty:
            vix_by_date = {ts.date(): float(v) for ts, v in vix_df_bt["Close"].items()}
            vix_aligned = pd.Series(
                [vix_by_date.get(d.date(), np.nan) for d in df.index]
            ).ffill().bfill()
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
    last_exit_idx = -100  # Change 1: tracks re-entry cooldown (2 trading days)

    # Change 1: gate rejection counters for comparison output
    gate_rejections: dict[str, int] = {
        "vix_too_high": 0,
        "overextended": 0,
        "momentum_disagreement": 0,
        "reentry_cooldown": 0,
    }
    total_buy_signals_raw = 0  # composite >= threshold signals before any gate

    # Diagnostic-only: per-event log of qualifying BUY signals that a gate blocked.
    # Visibility change only — does not affect signals, gate logic, or trade outcomes.
    # Only signals scoring >= this threshold are logged (a qualifying BUY already clears
    # the buy threshold, so low-score days that were never going to trade are excluded).
    REJECTION_SCORE_FLOOR = 40.0
    rejection_events: list[dict] = []

    def _log_rejection(idx: int, gate: str, score: float, detail: str) -> None:
        if score < REJECTION_SCORE_FLOOR:
            return
        raw_d = dates[idx]
        d_str = raw_d.strftime("%Y-%m-%d") if hasattr(raw_d, "strftime") else str(raw_d)[:10]
        rejection_events.append({
            "date":   d_str,
            "ticker": ticker,
            "gate":   gate,
            "score":  round(float(score), 1),
            "detail": detail,
        })

    test_start = TRAIN
    while test_start + TEST <= n:
        for idx in range(test_start, min(test_start + TEST, n)):
            r_t = returns[idx]
            # Composite score from price-derivable factors (momentum + vol-adjusted trend)
            # on the history available at this decision point. Earnings, insider, and
            # sentiment can't be reconstructed point-in-time historically, so they're
            # treated as unavailable and the surviving weights renormalise — the exact
            # mechanism the live engine uses when a factor is null. Entry mirrors live:
            # enter when this composite clears the buy threshold (no discrete Markov).
            composite = _backtest_composite(closes[: idx + 1])
            if composite is None:
                composite = 0.0

            # Portfolio update: earn r_t if already in position BEFORE this decision
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
                    # Mid-window exit on score deterioration (mirrors the live
                    # score-deterioration close) or max-hold expiry.
                    if composite < exit_threshold or hold_left <= 0:
                        portfolio *= (1.0 - TC_PER_SIDE)  # Change 1: exit transaction cost
                        trade_results.append(portfolio > trade_entry_val)
                        in_pos = False
                        last_exit_idx = idx
            else:
                daily_strat.append(0.0)
                if composite >= buy_threshold:
                    # Diagnostic score for this BUY signal (0–100): the composite itself.
                    # Logging only, never affects trade decisions.
                    buy_score = composite
                    spy_ok = (spy_above_ma is None) or bool(spy_above_ma[idx])
                    if macro_filter and not spy_ok:
                        _log_rejection(idx, "macro_circuit_breaker", buy_score,
                                       "SPY below its 200-day MA")
                    if not macro_filter or spy_ok:
                        total_buy_signals_raw += 1

                        # Change 1: VIX > 30 gate
                        if vix_series_bt is not None and idx < len(vix_series_bt):
                            if vix_series_bt[idx] > 30.0:
                                gate_rejections["vix_too_high"] += 1
                                _log_rejection(idx, "vix_too_high", buy_score,
                                               f"VIX={vix_series_bt[idx]:.1f} > 30")
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
                                _log_rejection(idx, "overextended", buy_score,
                                               f"price {price_ratio:.2f}× MA20 (>1.25), not top-quartile momentum")
                                continue

                        # Change 1: momentum disagreement gate
                        r3  = ret_3m_bt[idx]
                        r12 = ret_12m_bt[idx]
                        if not np.isnan(r3) and not np.isnan(r12):
                            if (r3 + r12) <= 0 or r3 < -0.10 or r12 < -0.10:
                                gate_rejections["momentum_disagreement"] += 1
                                _log_rejection(idx, "momentum_disagreement", buy_score,
                                               f"3m={r3:+.1%}, 12m={r12:+.1%}")
                                continue

                        # Change 1: re-entry cooldown (2 trading days after last exit)
                        if idx - last_exit_idx < 2:
                            gate_rejections["reentry_cooldown"] += 1
                            _log_rejection(idx, "reentry_cooldown", buy_score,
                                           f"{idx - last_exit_idx} days since last exit, cooldown=2")
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

    # Diagnostic-only: per-gate count of how often each gate was the primary blocker
    # (the first gate that fired for a logged signal), descending. Built from the
    # logged events so the summary and the event table always agree.
    gate_rejection_summary: dict[str, int] = {}
    for _ev in rejection_events:
        gate_rejection_summary[_ev["gate"]] = gate_rejection_summary.get(_ev["gate"], 0) + 1
    gate_rejection_summary = dict(
        sorted(gate_rejection_summary.items(), key=lambda kv: kv[1], reverse=True)
    )

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
        # Diagnostic-only: why qualifying BUY signals didn't convert to trades
        "gate_rejections":         rejection_events,
        "gate_rejection_summary":  gate_rejection_summary,
        # Change 1: gate comparison — how many BUY signals each gate filtered
        "gate_comparison": {
            "total_buy_signals_raw":     total_buy_signals_raw,
            "trades_after_gates":        trades_after_gates,
            "filters":                   gate_rejections,
            "transaction_cost_bps_per_side": int(TC_PER_SIDE * 10000),
            "notes": (
                "Entry uses composite score ≥ buy threshold (matches live). "
                "Composite is computed from momentum + vol-adjusted trend only; earnings, "
                "insider, sentiment, earnings_within_2d and sector_cap gates require "
                "real-time data unavailable in historical simulation."
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


def _volume_divergence_modifier(
    closes: np.ndarray, volumes: np.ndarray | None, ticker: str | None = None
) -> int:
    """Modifier (points) applied to the vol-adjusted trend score, capturing whether a
    price move is confirmed or contradicted by volume.

    Penalise price rising on declining volume (weak conviction) or falling on rising
    volume (confirmed selling); small bonus for price rising on rising volume (confirmed)
    or falling on declining volume (sellers exhausting). Fail-safe: any insufficient or
    missing volume data returns 0 so a volume failure can never affect the composite."""
    if volumes is None or len(volumes) < 20 or len(closes) < 20:
        return 0
    vol_ma20 = float(np.mean(volumes[-20:]))
    if vol_ma20 <= 0:
        return 0
    vol_recent = float(np.mean(volumes[-5:]))  # 5-day average smooths noise
    vol_ratio = vol_recent / vol_ma20
    price_trend = (float(closes[-1]) - float(closes[-20])) / float(closes[-20])

    if price_trend > 0.02 and vol_ratio < 0.8:
        modifier = -8   # price up, volume down — weak conviction
    elif price_trend > 0.02 and vol_ratio > 1.2:
        modifier = 5    # price up, volume up — confirmed move
    elif price_trend < -0.02 and vol_ratio > 1.2:
        modifier = -8   # price down, volume up — confirmed selling
    elif price_trend < -0.02 and vol_ratio < 0.8:
        modifier = 5    # price down, volume down — sellers exhausting
    else:
        modifier = 0

    logger.debug(
        "Volume divergence [%s]: price_trend=%.1f%% vol_ratio=%.2f modifier=%+d",
        ticker or "?", price_trend * 100.0, vol_ratio, modifier,
    )
    return modifier


def _vol_trend_score(
    closes: np.ndarray, volumes: np.ndarray | None = None, ticker: str | None = None
) -> tuple[float | None, dict | None]:
    """Return (0–100 score, display detail). Detail is the raw price/MA levels so the
    UI can show which moving averages the price is above/below — display only.

    When ``volumes`` is supplied, a volume-divergence modifier nudges the score at the
    margin (see _volume_divergence_modifier). Omitting volumes (e.g. the price-only
    backtest path) leaves the base MA-alignment score unchanged."""
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
    base_score = 50.0 + (raw - 0.5) * 100.0 * vol_factor
    # Volume divergence modifier (fail-safe: 0 when volume data is missing/insufficient)
    modifier = _volume_divergence_modifier(closes, volumes, ticker)
    score = max(0.0, min(100.0, base_score + modifier))
    detail = {
        "price": float(price),
        "ma20":  float(ma20),
        "ma50":  float(ma50),
        "ma200": float(ma200),
    }
    return float(np.clip(score, 0, 100)), detail


def _backtest_composite(closes: np.ndarray) -> float | None:
    """Composite score for the walk-forward backtests, from the factors that *can* be
    reconstructed point-in-time from price history alone: momentum and vol-adjusted
    trend. Earnings, insider, and sentiment have no historical per-day source, so they
    are treated as unavailable and the two surviving weights are renormalised — the same
    null-factor renormalisation the live composite (_compute_factors) applies. Returns
    None when neither factor has enough history yet."""
    available: list[tuple[float, float]] = []  # (weight, score)
    mom = _momentum_score(closes)
    if mom is not None:
        available.append((DEFAULT_FACTOR_WEIGHTS["momentum"], mom))
    vt, _ = _vol_trend_score(closes)
    if vt is not None:
        available.append((DEFAULT_FACTOR_WEIGHTS["vol_trend"], vt))
    total_w = sum(w for w, _ in available)
    if total_w <= 0:
        return None
    return sum(s * w for w, s in available) / total_w


def _extract_earnings_quarters(eh: pd.DataFrame | None, n: int = 4) -> list[dict]:
    """Pull the last ``n`` quarters out of yfinance's earnings_history frame.

    Shares its data source with _earnings_score (ticker.earnings_history) so we never
    add a separate earnings fetch. Returns newest→oldest; each row has the quarter date,
    EPS actual, EPS estimate, and surprise % (as a percentage, e.g. 4.2 not 0.042)."""
    if eh is None or len(eh) == 0:
        return []
    if "epsActual" not in eh.columns or "epsEstimate" not in eh.columns:
        return []
    rows: list[dict] = []
    for idx, row in eh.sort_index().tail(n).iterrows():
        est, act = row.get("epsEstimate"), row.get("epsActual")
        est = None if pd.isna(est) else float(est)
        act = None if pd.isna(act) else float(act)
        surprise_pct: float | None = None
        if est is not None and act is not None and est != 0:
            surprise_pct = round((act - est) / abs(est) * 100.0, 2)
        try:
            date_str = pd.Timestamp(idx).strftime("%Y-%m-%d")
        except Exception:
            date_str = str(idx)
        rows.append({
            "date": date_str,
            "eps_actual": act,
            "eps_estimate": est,
            "surprise_pct": surprise_pct,
        })
    rows.reverse()  # newest first
    return rows


def _earnings_score(ticker_obj: yf.Ticker) -> tuple[float | None, dict | None]:
    """Return (0–100 score, display detail). Detail carries the last two quarters'
    surprise % (act vs estimate, as fractions) so the UI can show the raw beats/misses."""
    try:
        eh = ticker_obj.earnings_history
        if eh is None or len(eh) < 2:
            return None, None
        # Most recent two quarters
        last2 = eh.sort_index().tail(2)
        # Staleness guard: earnings_history always returns the most recent rows regardless
        # of age, but reports only update ~quarterly. If the latest report is older than a
        # quarter + buffer, null the factor (like absent data) instead of contributing a
        # stale beats/misses score to the composite.
        most_recent = last2.index[-1]
        try:
            report_date = most_recent.to_pydatetime().replace(tzinfo=None)
            days_since = (datetime.now() - report_date).days
            if days_since > 120:  # older than ~1 quarter + 30 day buffer
                logger.debug("%s: earnings factor stale (%d days since last report) — nulling",
                             getattr(ticker_obj, "ticker", "?"), days_since)
                return None, {"stale": True, "days": days_since}
        except Exception:
            pass  # if date parse fails, proceed with existing data
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


def _fetch_insider(ticker: str) -> dict:
    """
    Change 3: insider activity from the EDGAR structured submissions API (not EFTS full-text).
    Fetches Form 4 filings for the exact company CIK to avoid false positives.
    P = open-market purchase (bullish), S = open-market sale (bearish) — awards/exercises excluded.
    Filer weight: Officer=1.0×, Director=0.7×, 10%+ holder=0.5×, other=0.3×.

    Single source of truth shared by the scored insider factor (_get_insider_score) and the
    /api/insider detail endpoint, so the Factor Breakdown UI and the composite never disagree.

    Returns a dict:
      ok                 — EDGAR reachable (CIK + submissions resolved). False ⇒ data unavailable.
      score              — 0-100 factor score, or None when there is no usable Form 4 data
                           (so the weight drops and the remaining factors renormalise).
      net_weighted       — role-weighted net shares (drives score + direction).
      net_shares         — raw (unweighted) net shares P−S, for display.
      transaction_count  — number of P/S transactions counted.
      filings_processed  — Form 4 filings successfully parsed.
      direction          — "buying" / "selling" / "neutral" (from net_weighted).
      transactions       — per-transaction line items for the detail view.
    """
    # 1-hour cache: return a fresh prior result without re-hitting EDGAR. Failures
    # are never stored, so a cache miss here always means a real (re)fetch is needed.
    with _INSIDER_LOCK:
        cached = _INSIDER_CACHE.get(ticker)
        if cached is not None and (datetime.utcnow() - cached[0]) < _INSIDER_TTL:
            logger.info("EDGAR cache hit: %s", ticker)
            return cached[1]

    ROLE_WEIGHTS = {"isOfficer": 1.0, "isDirector": 0.7, "isTenPercentOwner": 0.5}
    ROLE_LABELS  = {"isOfficer": "Officer", "isDirector": "Director", "isTenPercentOwner": "10% owner"}
    headers = {"User-Agent": "stock-tracker emmettmacken@gmail.com"}
    blank = {
        "ok": False, "score": None, "net_weighted": 0.0, "net_shares": 0,
        "transaction_count": 0, "filings_processed": 0, "direction": "neutral",
        "transactions": [],
    }

    def _store(result: dict) -> dict:
        """Cache an EDGAR-reachable result (ok=True) with a fresh UTC timestamp and
        return it. Failures (ok=False / exceptions) bypass this and stay uncached."""
        with _INSIDER_LOCK:
            _INSIDER_CACHE[ticker] = (datetime.utcnow(), result)
        return result

    try:
        cik = _get_cik(ticker)
        if not cik:
            return dict(blank)

        sub_url = f"https://data.sec.gov/submissions/CIK{cik}.json"
        resp = _http_client.get(sub_url, headers=headers, timeout=15.0)
        if resp.status_code != 200:
            return dict(blank)

        # EDGAR reachable from here on: ok=True even if no Form 4s exist in the window.
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
            # No Form 4s = no data, not a neutral signal. score stays None so the factor
            # is excluded from the composite and the remaining weights renormalise.
            return _store({**blank, "ok": True})

        cik_int = int(cik)
        net_weighted = 0.0
        net_shares = 0.0
        transaction_count = 0
        filings_processed = 0
        transactions: list[dict] = []

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

                owner_el = tree.find(".//reportingOwner/reportingOwnerId/rptOwnerName")
                owner = (owner_el.text or "").strip() if owner_el is not None else ""

                # Highest role weight for this filer (take max if multiple roles held)
                role_weight = 0.3
                role_label = "Other"
                for role_tag, w in ROLE_WEIGHTS.items():
                    el = tree.find(f".//reportingOwnerRelationship/{role_tag}")
                    if el is not None and (el.text or "").strip() in ("1", "true"):
                        if w > role_weight:
                            role_label = ROLE_LABELS[role_tag]
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

                    price_el = tx.find(".//transactionAmounts/transactionPricePerShare/value")
                    date_el  = tx.find(".//transactionDate/value")
                    try:
                        price = float(price_el.text) if price_el is not None and price_el.text else None
                    except (ValueError, TypeError):
                        price = None

                    signed = shares if code == "P" else -shares
                    net_weighted += signed * role_weight
                    net_shares   += signed
                    transaction_count += 1
                    transactions.append({
                        "owner": owner,
                        "role": role_label,
                        "type": "buy" if code == "P" else "sell",
                        "code": code,
                        "shares": int(shares),
                        "price": round(price, 2) if price is not None else None,
                        "date": (date_el.text or "").strip() if date_el is not None else None,
                    })

                filings_processed += 1
            except Exception:
                continue

        if filings_processed == 0:
            # Form 4s exist but all failed to parse → score None (drop weight, don't guess)
            return _store({**blank, "ok": True})

        score = 70.0 if net_weighted > 0 else (30.0 if net_weighted < 0 else 50.0)
        direction = "buying" if net_weighted > 0 else ("selling" if net_weighted < 0 else "neutral")
        return _store({
            "ok": True, "score": score, "net_weighted": net_weighted,
            "net_shares": int(net_shares), "transaction_count": transaction_count,
            "filings_processed": filings_processed, "direction": direction,
            "transactions": transactions,
        })
    except Exception as e:
        logger.warning("Insider fetch failed for %s: %s", ticker, e)
        return dict(blank)


def _get_insider_score(ticker: str) -> float | None:
    """0-100 insider factor score, or None on EDGAR failure / no usable Form 4 data.
    Thin wrapper over the shared _fetch_insider so the factor and detail endpoint agree."""
    return _fetch_insider(ticker)["score"]


def _fetch_sector(ticker: str, attempts: int = 3, delay: float = 2.0) -> Optional[str]:
    """Fetch a ticker's sector from yfinance, retrying transient failures.

    Returns the sector string on success, or None if every attempt fails (so the caller
    can avoid caching a failure). The signal job calls .info for the whole watchlist
    concurrently (ThreadPoolExecutor, 2 workers), and Yahoo rate-limits that burst by
    returning a *partial/empty .info dict with no exception raised* — the silent path
    that previously produced "Unknown". We treat a missing sector the same as an error,
    retry it, and log every failure so it's visible in the logs rather than swallowed."""
    for attempt in range(1, attempts + 1):
        try:
            info = yf.Ticker(ticker).info or {}
            sector = info.get("sector")
            if sector:
                return sector
            logger.warning("Sector lookup for %s returned no sector field "
                           "(attempt %d/%d, .info keys=%d — likely rate-limited)",
                           ticker, attempt, attempts, len(info))
        except Exception as e:
            logger.warning("Sector lookup failed for %s (attempt %d/%d): %s",
                           ticker, attempt, attempts, e)
        if attempt < attempts:
            time.sleep(delay)
    logger.error("Sector lookup gave up for %s after %d attempts", ticker, attempts)
    return None


def _get_sector(ticker: str) -> str:
    """Return the sector string for a ticker, cached for 24 h.

    Only *successful* lookups are cached. A transient failure returns "Unknown" without
    writing to the cache, so the next call retries instead of serving a stale "Unknown"
    for the full 24h TTL — the bug that previously froze the sector-exposure panel on
    "Unknown" for every position after one rate-limited burst."""
    # Change 5: lock prevents TOCTOU race during parallel signal job
    with _SECTOR_LOCK:
        cached_sector, ts = _SECTOR_CACHE.get(ticker, ("", 0.0))
        if cached_sector and (time.time() - ts) < _SECTOR_TTL:
            return cached_sector
    sector = _fetch_sector(ticker)
    if sector:
        with _SECTOR_LOCK:
            _SECTOR_CACHE[ticker] = (sector, time.time())
        return sector
    return "Unknown"


def _get_company_info(ticker: str) -> dict:
    """Return cached company info for a ticker (name, sector, industry, business
    summary, a few trader-relevant fields, and the last ~4 quarters of earnings).

    Cached for 7 days — this data essentially never changes (and only quarterly for
    earnings), so it's cached far more aggressively than price/factor data. Uses the
    same ticker.info source as _get_sector and seeds the 24h sector cache from the same
    fetch, so the sector is never fetched twice."""
    ticker = ticker.upper()
    with _COMPANY_LOCK:
        cached, ts = _COMPANY_CACHE.get(ticker, ({}, 0.0))
        if cached and (time.time() - ts) < _COMPANY_TTL:
            return cached

    # Fetch .info with retry/backoff, mirroring _fetch_sector. A successful fetch has a
    # usable name (longName/shortName); a partial/empty dict — Yahoo's silent response to
    # the parallel signal-job burst — is treated as a transient failure and retried. raw
    # stays {} when every attempt fails, so the info dict below degrades to all-None.
    raw: dict = {}
    tk = None
    name: str | None = None
    for attempt in range(1, _COMPANY_FETCH_ATTEMPTS + 1):
        try:
            tk = yf.Ticker(ticker)
            raw = tk.info or {}
            name = raw.get("longName") or raw.get("shortName") or None
            if name:
                break
            logger.warning("Company info for %s returned no name field "
                           "(attempt %d/%d, .info keys=%d — likely rate-limited)",
                           ticker, attempt, _COMPANY_FETCH_ATTEMPTS, len(raw))
            raw = {}
        except Exception as e:
            logger.warning("Company info lookup failed for %s (attempt %d/%d): %s",
                           ticker, attempt, _COMPANY_FETCH_ATTEMPTS, e)
            raw = {}
        if attempt < _COMPANY_FETCH_ATTEMPTS:
            time.sleep(_COMPANY_FETCH_DELAY)

    sector = raw.get("sector") or "Unknown"
    # Seed the sector cache from this same fetch so _get_sector won't re-fetch .info —
    # but only on a real hit. Never poison the cache with "Unknown" from a partial /
    # rate-limited .info response, which would freeze the sector panel for the 24h TTL.
    if sector != "Unknown":
        with _SECTOR_LOCK:
            _SECTOR_CACHE[ticker] = (sector, time.time())
    info = {
        "ticker":             ticker,
        "name":               name,
        "sector":             sector,
        "industry":           raw.get("industry") or None,
        "summary":            raw.get("longBusinessSummary") or None,
        # 2–4 trader-relevant additions that yfinance reliably populates
        "market_cap":         raw.get("marketCap"),
        "trailing_pe":        raw.get("trailingPE"),
        "forward_pe":         raw.get("forwardPE"),
        "dividend_yield":     raw.get("dividendYield"),
        "fifty_two_week_high": raw.get("fiftyTwoWeekHigh"),
        "fifty_two_week_low":  raw.get("fiftyTwoWeekLow"),
        # Financials panel (stock detail page). All from this same .info fetch — no
        # extra yfinance call. Verified to populate reliably across a diverse ticker
        # set; individual nulls are hidden client-side.
        "peg_ratio":          raw.get("pegRatio"),
        "price_to_sales":     raw.get("priceToSalesTrailing12Months"),
        "price_to_book":      raw.get("priceToBook"),
        "ev_to_ebitda":       raw.get("enterpriseToEbitda"),
        "profit_margin":      raw.get("profitMargins"),
        "operating_margin":   raw.get("operatingMargins"),
        "return_on_equity":   raw.get("returnOnEquity"),
        "revenue_growth":     raw.get("revenueGrowth"),
        "debt_to_equity":     raw.get("debtToEquity"),
        "current_ratio":      raw.get("currentRatio"),
        "free_cash_flow":     raw.get("freeCashflow"),
        "beta":               raw.get("beta"),
        "average_volume":     raw.get("averageVolume"),
        "payout_ratio":       raw.get("payoutRatio"),
    }
    earnings: list[dict] = []
    if name and tk is not None:
        try:
            earnings = _extract_earnings_quarters(tk.earnings_history, n=4)
        except Exception as e:
            logger.warning("Earnings history lookup failed for %s: %s", ticker, e)

    info["earnings"] = earnings
    info = _sanitize_json(info)
    # Only cache a successful fetch (name present). A name-less result is returned for
    # display but NOT cached, so the next call retries instead of freezing name=None for
    # the full 7-day TTL — the cache-poisoning bug that mirrored the old sector "Unknown".
    if name:
        with _COMPANY_LOCK:
            _COMPANY_CACHE[ticker] = (info, time.time())
    else:
        logger.error("Company info gave up for %s after %d attempts — not caching",
                     ticker, _COMPANY_FETCH_ATTEMPTS)
    return info


def _composite_signal(score: Optional[float]) -> str:
    """Map a 0–100 composite score to a BUY/HOLD/SELL badge for display. Mirrors the
    verdict bands (≥63 = buy zone, <45 = weak). Display only — the live trader gates on
    the macro-aware threshold, not this static mapping."""
    if score is None:
        return "HOLD"
    if score >= 63.0:
        return "BUY"
    if score < 45.0:
        return "SELL"
    return "HOLD"


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

    # Regime label based on the Kalman-smoothed Gaussian-HMM bull probability.
    if smoothed_bull_prob_last > 0.65:
        regime_label = "bull"
    elif smoothed_bull_prob_last < 0.35:
        regime_label = "bear"
    else:
        regime_label = "transition"

    mom_score      = _momentum_score(closes)
    vt_score, vt_detail     = _vol_trend_score(closes, volumes=vol, ticker=ticker)
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
        "momentum":  {"score": round(mom_score, 2)      if mom_score      is not None else None, "weight": weights["momentum"],  "null": mom_score      is None},
        "vol_trend": {"score": round(vt_score, 2)        if vt_score       is not None else None, "weight": weights["vol_trend"], "null": vt_score       is None},
        "earnings":  {"score": round(earn_score, 2)      if earn_score     is not None else None, "weight": weights["earnings"],  "null": earn_score     is None},
        "insider":   {"score": round(insider_score, 2)   if insider_score  is not None else None, "weight": weights["insider"],   "null": insider_score  is None},
        # Change 2: sentiment factor; null when API unavailable — weight renormalises automatically
        "sentiment": {"score": round(sentiment_score, 2) if sentiment_score is not None else None, "weight": weights.get("sentiment", DEFAULT_FACTOR_WEIGHTS.get("sentiment", 0.12909)), "null": sentiment_score is None},
    }

    available = {k: v for k, v in factors.items() if not v["null"]}
    total_w = sum(v["weight"] for v in available.values())
    composite = sum(v["score"] * v["weight"] / total_w for v in available.values()) if total_w > 0 else None

    # Every factor was unavailable (total_w == 0) → there is no score, not a score of 0.
    # Return None like the price-fetch failure path (see the `return None` above) so the
    # caller treats this as data_unavailable and never acts on a degenerate composite —
    # in particular, never fires a spurious score-deterioration SELL.
    if composite is None:
        logger.warning("All factors unavailable for %s — returning None (data_unavailable)", ticker)
        return None

    non_null_scores = [v["score"] for v in factors.values() if not v["null"] and v["score"] is not None]
    min_factor_score = min(non_null_scores) if non_null_scores else None

    # Display BUY/HOLD/SELL badge derived from the composite (the live trader uses the
    # macro-aware threshold; this is a static, display-only mapping for the watchlist).
    signal = _composite_signal(round(composite, 2))

    # Display-only true trailing returns: end at the latest close (no 21-day skip).
    # The skip is intentional for _momentum_score's 12-1 z-score (avoids short-term
    # reversal) but is misleading for a field the UI labels plainly "3-month return",
    # so the displayed numbers measure today vs 63/252 trading days ago.
    ret_3m  = float(closes[-1] / closes[-64]  - 1.0) if len(closes) >= 64  else None
    ret_12m = float(closes[-1] / closes[-253] - 1.0) if len(closes) >= 253 else None

    # Skip-21 returns feeding the live momentum-disagreement gate (matches the
    # backtest's ret_3m_bt/ret_12m_bt, which skip the most recent 21 days). Kept
    # separate from the display fields above so the gate's input never changes.
    c_lag        = closes[:-21] if len(closes) > 21 else closes
    ret_3m_skip  = float(c_lag[-1] / c_lag[-63]  - 1.0) if len(c_lag) >= 63  else None
    ret_12m_skip = float(c_lag[-1] / c_lag[-252] - 1.0) if len(c_lag) >= 252 else None

    # Display-only: day-over-day price change for the snapshot/homepage cards.
    prev_close = float(closes[-2]) if len(closes) >= 2 else float(closes[-1])
    price_change_pct = (
        round((float(closes[-1]) - prev_close) / prev_close * 100, 4) if prev_close else 0.0
    )

    atr = _atr_from_df(df)
    rets_21 = np.diff(closes[-22:]) / closes[-22:-1] if len(closes) >= 22 else np.array([0.0])
    vol_21d = float(rets_21.std())

    # Display-only company info (7-day cache). Reuses a single .info fetch that also
    # seeds the 24h sector cache, so sector is never fetched twice.
    company = _get_company_info(ticker)

    result = {
        "ticker":              ticker,
        "factors":             factors,
        "composite_score":     round(composite, 2),
        "min_factor_score":    round(min_factor_score, 2) if min_factor_score is not None else None,
        "signal":              signal,
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
        # Internal (not displayed): skip-21 momentum returns for the live disagreement gate
        "ret_3m_skip":         ret_3m_skip,
        "ret_12m_skip":        ret_12m_skip,
        # Display-only raw breakdowns for the stock detail page (never used in scoring)
        "vol_trend_detail":    vt_detail,
        "earnings_detail":     earn_detail,
        "atr":                 atr,
        "vol_21d":             vol_21d,
        "sentiment_score":     round(sentiment_score, 2) if sentiment_score is not None else None,
        # Display-only: cached sector tag (sourced from the same .info fetch the
        # concentration gate's _get_sector uses) so the watchlist can offer a sector
        # filter. Never used in scoring.
        "sector":              company.get("sector", "Unknown"),
        # Display-only: company long name so the watchlist card can show "AAPL — Apple Inc."
        "company_name":        company.get("name"),
    }
    with _FACTORS_LOCK:
        _FACTORS_CACHE[ticker] = (result, time.time())
    return result


def _factors_payload(result: dict) -> dict:
    """The display payload (FactorScoreData) — full breakdown minus internal-only fields."""
    return {k: v for k, v in result.items() if k not in ("current_price", "atr", "vol_21d")}


def _write_snapshot(ticker: str, result: dict) -> None:
    """Persist a ticker's freshly computed factors as a cached display snapshot.

    Display-only: never affects trading logic. `signal` is the composite-derived
    BUY/SELL/HOLD badge shown on the card.
    """
    db.upsert_snapshot(
        ticker=ticker,
        composite_score=result.get("composite_score"),
        signal=result.get("signal"),
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
                "signal":           res["signal"],
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


@app.get("/api/company/{ticker}")
def get_company(ticker: str):
    """Cached company profile: name, sector, industry, business summary, a few
    trader-relevant fields (market cap, P/E, dividend yield, 52-week range) and the
    last ~4 quarters of earnings (date, EPS actual/estimate, surprise %)."""
    return _get_company_info(ticker.upper())


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


@app.get("/api/macro-regime")
def get_macro_regime():
    """Current SPY/200d-MA regime with provenance, so the active buy threshold can be
    inspected without grepping backend logs. `source` is "live" when SPY data was
    fetched this cycle, "fallback" when it failed and spy_above defaulted to True."""
    d = _macro_regime_detail()
    return {
        "spy_above":       d["spy_above"],
        "spy_price":       d["spy_price"],
        "spy_ma200":       d["spy_ma200"],
        "source":          d["source"],
        "fallback_reason": d["fallback_reason"],
    }


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
    """Insider detail for the Factor Breakdown UI. Backed by the same EDGAR
    structured-submissions fetch as the scored insider factor (_fetch_insider), so the
    detail view and the composite factor always describe the same underlying data."""
    ticker = ticker.upper()
    data = _fetch_insider(ticker)
    # "available" mirrors the scored factor: present iff there is usable Form 4 data
    # (data["score"] is not None). EDGAR-unreachable and no-filings both fall here, matching
    # the factor showing null — so the detail view and composite never tell different stories.
    if data["score"] is None:
        return {"available": False, "ticker": ticker}
    return {
        "available": True,
        "ticker": ticker,
        "score": data["score"],
        "net_shares": data["net_shares"],
        "transaction_count": data["transaction_count"],
        "direction": data["direction"],
        "transactions": data["transactions"],
        "period_days": 30,
    }


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


def _correlation_penalty(
    candidate_ticker: str,
    held_tickers: list[str],
    prices: dict[str, np.ndarray],
) -> tuple[float, float, Optional[str]]:
    """Position-size penalty for a candidate that moves with already-sized tickers.

    Computes the Pearson correlation of 60-day daily returns between `candidate_ticker`
    and each ticker in `held_tickers`, then applies the system-wide penalty curve: for
    the strongest correlation c > 0.7, penalty = 1 - (c - 0.7) / 0.3 * 0.5 (so c=0.70 → 1.0,
    c=1.0 → 0.5); a correlation at or below 0.7 incurs no penalty (1.0).

    `prices` maps each ticker to its close-price array; a ticker missing from `prices`,
    with fewer than 61 closes, or with fewer than 10 overlapping return days is skipped.
    Shared by /api/portfolio/sizing and the live trader's _position_dollars so the two
    paths apply an identical penalty for the same inputs — keeping live and backtest in sync.

    Returns (penalty, max_corr, correlated_ticker), where correlated_ticker is the held
    name driving the penalty (None when none qualifies).
    """
    cand_c = prices.get(candidate_ticker)
    if cand_c is None or len(cand_c) < 61:
        return 1.0, 0.0, None
    cand_r = pd.Series(np.diff(cand_c[-61:]) / cand_c[-61:-1])

    max_corr = 0.0
    correlated_ticker: Optional[str] = None
    for held in held_tickers:
        if held == candidate_ticker:
            continue
        held_c = prices.get(held)
        if held_c is None or len(held_c) < 61:
            continue
        held_r = pd.Series(np.diff(held_c[-61:]) / held_c[-61:-1])
        pair = pd.DataFrame({"a": cand_r, "b": held_r}).dropna()
        if len(pair) < 10:
            continue
        c_val = float(pair["a"].corr(pair["b"]))
        if not np.isnan(c_val) and c_val > max_corr:
            max_corr = c_val
            correlated_ticker = held

    if max_corr > 0.7:
        return 1.0 - (max_corr - 0.7) / 0.3 * 0.5, max_corr, correlated_ticker
    return 1.0, max_corr, correlated_ticker


@app.post("/api/portfolio/sizing")
def portfolio_sizing(req: SizingRequest):
    try:
        tickers = [t.upper() for t in req.tickers]
        signals = {k.upper(): v for k, v in req.signals.items()}

        # Fetch 120-day closes for all tickers in parallel (was a sequential loop ~4s for a
        # full watchlist). Every ticker still gets an entry in both maps — a failed fetch
        # falls back to an empty close series and a 0.25 vol prior, exactly as before.
        closes_map: dict[str, np.ndarray] = {t: np.array([]) for t in tickers}
        vols_map: dict[str, float] = {t: 0.25 for t in tickers}

        def _fetch_closes(t: str) -> tuple[str, Optional[np.ndarray]]:
            try:
                df = fetch_ohlcv(t, days=120, min_bars=30)
                return t, df["Close"].values.astype(float)
            except Exception:
                return t, None

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = {pool.submit(_fetch_closes, t): t for t in tickers}
            for fut in as_completed(futures):
                t, c = fut.result()
                if c is not None:
                    closes_map[t] = c
                    vols_map[t] = _realised_vol(c)

        # Scores
        scores = {t: signals[t].composite_score if t in signals else 50.0 for t in tickers}
        sorted_tickers = sorted(tickers, key=lambda t: -scores[t])

        # Correlation penalty: shrink a ticker's weight when it moves with an
        # already higher-scored ticker in this batch. Computed via the shared
        # _correlation_penalty helper so the live trader (_position_dollars) and this
        # endpoint apply an identical penalty for the same inputs.
        corr_penalties: dict[str, float] = {}
        for i, t1 in enumerate(sorted_tickers):
            penalty, _, _ = _correlation_penalty(t1, sorted_tickers[:i], closes_map)
            corr_penalties[t1] = penalty

        # Kelly allocations
        kelly_fracs: dict[str, float] = {}
        for t in tickers:
            sig = signals.get(t)
            score = sig.composite_score if sig else 50.0
            rvol = vols_map[t]
            kf = _kelly_fraction(score, rvol) * corr_penalties[t]
            kelly_fracs[t] = kf

        # Normalize so total Kelly allocation never exceeds 100% (shared with live trader)
        kelly_fracs = _normalize_portfolio_sizing(kelly_fracs, PORTFOLIO_KELLY_CAP)

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

        with ThreadPoolExecutor(max_workers=2) as pool:
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

        # Precompute features per ticker: closing prices + simple returns. (Volume is no
        # longer needed — the discrete-Markov state machine that used it was removed.)
        features: dict[str, tuple] = {}
        for i, t in enumerate(valid):
            c = combined_arr[:, i]
            ret = np.diff(c) / c[:-1]
            features[t] = (c, ret)

        # Change 1: 7bps commission + 0.1% slippage per side
        TC_PER_SIDE_PB = 0.0017
        # Entry/exit now mirror the live trader and the single-ticker backtest: allocate
        # to tickers whose composite clears the buy threshold, exit on score deterioration.
        buy_threshold = float(db.get_config("bull_threshold", "63"))
        exit_threshold = 40.0

        # Walk-forward
        portfolio_val = req.capital
        equity_curve: list[dict] = []
        rebalance_events: list[dict] = []
        per_ticker_contrib: dict[str, float] = {t: 0.0 for t in valid}
        mid_window_exits = 0  # Change 1: counter for score-deterioration early exits
        # Profit-take bookkeeping: per-ticker entry close (the +15% trim basis), the set of
        # names already trimmed this holding (trim once, not every bar), and a run counter.
        entry_prices_bt: dict[str, float] = {}
        profit_taken_bt: set[str] = set()
        profit_takes_bt = 0

        # SPY benchmark
        spy_closes = None
        spy_base = None
        if "SPY" in dfs:
            spy_aligned = dfs["SPY"]["Close"].reindex(dates_idx).ffill()
            spy_closes = spy_aligned.values

        # Entry gates added to narrow the divergence from the live trader and the
        # single-ticker backtest, which both refuse new entries under these conditions.
        # These are computed POINT-IN-TIME (one value per historical date), mirroring the
        # single-ticker backtest — not from the live _macro_regime() snapshot, which only
        # describes today and would be wrong applied across historical rebalance points.
        #
        # Macro gate: SPY below its 200-day MA → no new entries this rebalance (go to cash).
        spy_above_ma: Optional[np.ndarray] = None
        if "SPY" in dfs:
            try:
                spy_arr = dfs["SPY"]["Close"].reindex(dates_idx).ffill().bfill().values.astype(float)
                spy_ma200_arr = pd.Series(spy_arr).rolling(200, min_periods=1).mean().values
                spy_above_ma = spy_arr > spy_ma200_arr  # length n_days
            except Exception:
                spy_above_ma = None  # fail open: don't block on SPY data issues

        # VIX gate: VIX > 30 → no new entries this rebalance. Align by calendar date: the
        # ^VIX feed is tz-aware in a different zone (CT) than dates_idx (ET), so a direct
        # timestamp reindex would miss on every row. Any date still missing (or a failed
        # fetch) leaves NaN and is treated as fail-open by the gate test below.
        vix_series_pb: Optional[np.ndarray] = None
        try:
            vix_df_pb = yf.Ticker("^VIX").history(period="2y")
            if not vix_df_pb.empty:
                vix_by_date = {ts.date(): float(v) for ts, v in vix_df_pb["Close"].items()}
                vix_series_pb = pd.Series(
                    [vix_by_date.get(d.date(), np.nan) for d in dates_idx]
                ).ffill().bfill().values.astype(float)  # length n_days
        except Exception:
            vix_series_pb = None  # fail open: skip VIX gate if data unavailable

        # Re-entry cooldown: a ticker exited mid-window on score deterioration may not be
        # re-bought until 2 trading days have passed (mirrors the live trader / single-ticker
        # backtest). Only mid-window deterioration exits set this — the routine window-boundary
        # rebalance is not a "non-signal exit" and must not trigger a cooldown (every ticker
        # would otherwise sit 1 day from its prior window and never re-enter).
        last_exit_idx: dict[str, int] = {t: -100 for t in valid}

        # Remaining divergence (intentional, not fixed here): the live trader and single-ticker
        # backtest also apply overextension and 3m/12m momentum-disagreement gates. Those need
        # intra-window per-bar price context this rebalancing model doesn't evaluate per ticker,
        # so they are NOT replicated. The portfolio backtest is therefore now CLOSER TO but
        # still NOT IDENTICAL to live behaviour.
        test_start = TRAIN
        while test_start + TEST <= n_days - 1:
            ts = test_start - TRAIN
            te = test_start

            # Compute vol-targeted weights at this rebalance point. Each ticker's signal
            # comes from its composite score (momentum + vol-adjusted trend) on the price
            # history available here — the same composite the live trader gates on.
            rvols: dict[str, float] = {}
            signals_window: dict[str, str] = {}
            composites_window: dict[str, float] = {}  # point-in-time composite per ticker

            for t in valid:
                c, ret = features[t]
                tr_rets = ret[ts:te]
                if len(tr_rets) < 22:
                    rvols[t] = 0.25
                    signals_window[t] = "HOLD"
                    composites_window[t] = 0.0
                    continue
                rvols[t] = tr_rets[-21:].std() * np.sqrt(252)
                composite = _backtest_composite(c[: te + 1])
                composites_window[t] = composite if composite is not None else 0.0

                # Fix 3 — HMM bull-confidence gate (issue 10). Per-bar smoothed HMM state
                # can't be reconstructed point-in-time in this rebalancing model, so the
                # composite is used as a conservative proxy: require composite >= 70,
                # approximating the live trader's smoothed_bull_prob >= 0.70 entry gate.
                if composite is None or composite < 70:
                    signals_window[t] = "HOLD"
                    continue

                # Fix 4 — regime-adaptive threshold (issue 11). In a bear regime (SPY below
                # its 200-day MA) the live trader raises the buy bar; mirror that with an 80
                # threshold, falling back to the normal bull threshold otherwise. spy_above_ma
                # may be None (data fail-open) → treat as bull regime.
                bull_regime = (spy_above_ma is None) or bool(spy_above_ma[test_start])
                regime_threshold = buy_threshold if bull_regime else 80.0
                is_buy = composite >= regime_threshold
                # Re-entry cooldown: block a BUY within 2 trading days of a mid-window exit.
                if is_buy and (test_start - last_exit_idx[t]) < 2:
                    is_buy = False
                signals_window[t] = "BUY" if is_buy else "HOLD"

            # Market-wide entry gates evaluated at this rebalance date. When either blocks,
            # no new entries are taken and the portfolio holds cash for the window (we do NOT
            # fall back to equal weight, which would defeat the gate).
            macro_ok = (spy_above_ma is None) or bool(spy_above_ma[test_start])
            # Fail open: only a real reading above 30 blocks (NaN > 30 is False), matching
            # the single-ticker backtest so a VIX data gap never silently kills the run.
            vix_ok = (vix_series_pb is None) or not (vix_series_pb[test_start] > 30.0)
            entries_allowed = macro_ok and vix_ok
            if not entries_allowed:
                for t in valid:
                    signals_window[t] = "HOLD"

            # Fix 2 — no equal-weight fallback (issue 9). When nothing signals BUY the
            # portfolio holds cash; we never deploy capital into names that didn't signal.
            buy_tickers = [t for t in valid if signals_window[t] == "BUY"]

            # Fix 1 — _correlation_penalty-aware, score-ordered sizing (issues 8 + 13),
            # replacing pure inverse-vol weighting. Highest-conviction names are sized first
            # so the correlation penalty shrinks later names that move with them, mirroring
            # /api/portfolio/sizing and the live _position_dollars path. All price slices are
            # point-in-time (rows 0..test_start inclusive) — no look-ahead.
            buy_tickers_sorted = sorted(
                buy_tickers, key=lambda t: composites_window.get(t, 0.0), reverse=True
            )
            hist_prices = {t: combined_arr[: test_start + 1, valid.index(t)] for t in valid}

            sized: dict[str, float] = {}
            held_so_far: list[str] = []
            for t in buy_tickers_sorted:
                # Vol-target base weight (target ~1% vol contribution), matching _position_dollars.
                rvol = rvols.get(t, 0.25)
                base_weight = 0.01 / max(rvol, 0.001)
                # Conviction multiplier from composite score (75→1×, 95→1.5×).
                score = composites_window.get(t, buy_threshold)
                conviction = min(1.0 + max(0.0, score - 75.0) / 40.0, 1.5)
                # Correlation penalty against already-sized (higher-conviction) names.
                penalty, _, _ = _correlation_penalty(t, held_so_far, hist_prices)
                # Per-position hard cap at 10% of the portfolio (matches _position_dollars).
                weight = min(base_weight * conviction * penalty, 0.10)
                sized[t] = weight
                held_so_far.append(t)

            # Normalize so aggregate exposure never exceeds 1.0 (PORTFOLIO_KELLY_CAP equivalent).
            total_w = sum(sized.values())
            if total_w > 1.0:
                sized = {t: w / total_w for t, w in sized.items()}

            # Fix 5 — drop sub-floor positions (POSITION_FLOOR_PCT = 0.5%) not worth opening,
            # then re-normalize the survivors against the same cap.
            weights = {t: w for t, w in sized.items() if w >= POSITION_FLOOR_PCT}
            total_w = sum(weights.values())
            if total_w > 1.0:
                weights = {t: w / total_w for t, w in weights.items()}
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

            # Profit-take bookkeeping: record each newly entered ticker's entry close (basis
            # kept across windows while the name stays held), and drop the basis/flag for any
            # name not held this window so a later re-entry is measured from a fresh entry.
            for t in valid:
                if weights.get(t, 0.0) > 0:
                    entry_prices_bt.setdefault(
                        t, float(combined_arr[test_start, valid.index(t)])
                    )
                else:
                    entry_prices_bt.pop(t, None)
                    profit_taken_bt.discard(t)

            # Simulate TEST days with these weights
            for idx in range(test_start, min(test_start + TEST, n_days - 1)):
                # Change 1: mid-window exit on composite score deterioration (mirrors the
                # live score-deterioration close; replaces the old discrete-Markov SELL).
                for t in valid:
                    if live_weights.get(t, 0.0) <= 0:
                        continue
                    c_t, _ret_t = features[t]
                    if idx < 1 or idx >= len(c_t):
                        continue

                    # Profit-take: halve the weight once this name is +15% above its entry
                    # close. The remaining half keeps running for the rest of the window; the
                    # position stays open, so no re-entry cooldown is armed (mirrors the live
                    # profit-take which leaves the other half under the trailing stop).
                    if t not in profit_taken_bt:
                        entry_px = entry_prices_bt.get(t)
                        if entry_px and (c_t[idx] - entry_px) / entry_px >= 0.15:
                            half_weight = live_weights[t] * 0.5
                            # Charge exit cost on the closed half only
                            portfolio_val *= (1.0 - TC_PER_SIDE_PB * half_weight)
                            live_weights[t] = half_weight
                            profit_taken_bt.add(t)
                            profit_takes_bt += 1
                            continue

                    composite = _backtest_composite(c_t[: idx + 1])
                    if composite is not None and composite < exit_threshold:
                        # Apply exit cost proportional to this position's weight
                        portfolio_val *= (1.0 - TC_PER_SIDE_PB * live_weights[t])
                        live_weights[t] = 0.0
                        mid_window_exits += 1
                        last_exit_idx[t] = idx  # arm the re-entry cooldown
                        # Full exit: clear the profit-take basis so a re-entry starts fresh.
                        entry_prices_bt.pop(t, None)
                        profit_taken_bt.discard(t)

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
            "profit_takes": profit_takes_bt,
            # Change 1: gate comparison showing mid-window exit impact and cost drag
            "gate_comparison": {
                "total_rebalances": (n_days - TRAIN) // TEST,
                "mid_window_exits": mid_window_exits,
                "profit_takes": profit_takes_bt,
                "transaction_cost_bps_per_side": int(TC_PER_SIDE_PB * 10000),
                "notes": "mid_window_exits fired when a held ticker's composite fell below the deterioration threshold; profit_takes halved a position once it reached +15% above entry",
            },
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ═══════════════════════════════════════════════════════════════════════════════
# AUTOMATION HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _macro_regime_detail() -> dict:
    """Full macro-regime snapshot with provenance. Cached for _MACRO_TTL seconds.

    Returns a dict: spy_above, spy_price, spy_ma200, vix, source ("live"|"fallback"),
    fallback_reason (str|None). `_macro_regime()` is a thin (spy_above, vix) wrapper.
    """
    global _MACRO_CACHE
    # Change 5: lock prevents two threads from both finding the cache stale and double-fetching
    with _MACRO_LOCK:
        if _MACRO_CACHE is not None and (time.time() - _MACRO_CACHE[1]) < _MACRO_TTL:
            return _MACRO_CACHE[0]

    # SPY vs its 200d MA. Retry transient yfinance failures (timeout / rate limit /
    # empty response) up to 3× with 2s delays before falling back. Every attempt is
    # logged with the exact exception so production failures are diagnosable.
    spy_above = True
    spy_price: Optional[float] = None
    spy_ma200: Optional[float] = None
    spy_source = "fallback"
    fallback_reason: Optional[str] = None
    last_failure: Optional[str] = None
    for attempt in range(1, 4):
        try:
            h = yf.Ticker("SPY").history(period="1y")
            if len(h) >= 200:
                spy_price = float(h["Close"].iloc[-1])
                spy_ma200 = float(h["Close"].iloc[-200:].mean())
                spy_above = spy_price > spy_ma200
                spy_source = "live"
                fallback_reason = None
                logger.info(
                    "Macro: SPY live (attempt %d) — price=%.2f ma200=%.2f above_200d=%s",
                    attempt, spy_price, spy_ma200, spy_above,
                )
                break
            # Data parsing issue: not enough history to compute a 200d MA.
            last_failure = f"insufficient history: {len(h)} rows (<200) from yf.Ticker('SPY').history(period='1y')"
            logger.warning("Macro: SPY fetch attempt %d/3 unusable — %s", attempt, last_failure)
        except Exception as e:
            last_failure = f"{type(e).__name__}: {e}"
            logger.warning(
                "Macro: SPY fetch attempt %d/3 failed — url=yf.Ticker('SPY').history(period='1y'): %s",
                attempt, last_failure,
            )
        if attempt < 3:
            time.sleep(2)
    if spy_source == "fallback":
        # Fail OPEN to a bull regime. The market sits above its 200d MA the large
        # majority of the time; failing closed to the stricter bear threshold (80)
        # would silently block almost every trade whenever the data feed hiccups.
        spy_above = True
        fallback_reason = last_failure or "unknown"
        logger.warning(
            "Macro: SPY 200d MA unavailable after 3 attempts (%s) — "
            "falling back to spy_above=True (bull-regime default)",
            fallback_reason,
        )

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

    detail = {
        "spy_above":       spy_above,
        "spy_price":       spy_price,
        "spy_ma200":       spy_ma200,
        "vix":             vix,
        "source":          spy_source,
        "fallback_reason": fallback_reason,
    }
    with _MACRO_LOCK:
        _MACRO_CACHE = (detail, time.time())
    return detail


def _macro_regime() -> tuple[bool, float]:
    """Returns (spy_above_200d_ma, vix_level). Thin wrapper over _macro_regime_detail()."""
    d = _macro_regime_detail()
    return d["spy_above"], d["vix"]


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
    except Exception as exc:
        # Fail OPEN (return False → allow the trade) when earnings data is genuinely
        # unavailable, but never silently: surface the failure in server logs and as a
        # flagged signal_log row so the gate shows up as uncertain in the UI rather than
        # an invisible pass right before a possible earnings announcement.
        logger.warning("%s: earnings_within_2d gate data unavailable: %s", ticker, exc)
        db.log_signal(ticker, None, None, "evaluated",
                      f"earnings_check_failed:{exc}", None, None)
        return False  # fail open, do NOT cache — retry on next signal job run
    with _EARNINGS_LOCK:
        _EARNINGS_CACHE[ticker] = (result, time.time())
    return result


def _upcoming_earnings_for_ticker(ticker: str) -> Optional[dict]:
    """Next upcoming earnings date + last two EPS surprises for one ticker, cached 1h.

    Shares its yfinance fetch pattern with _earnings_within_days (ticker.calendar) and its
    surprise logic with _earnings_score (ticker.earnings_history), both off a single Ticker
    object. Returns None — and caches None — when the calendar has no upcoming date, so the
    caller skips the ticker without failing the whole request. days_until is intentionally
    not cached (it's recomputed per request so it stays correct across a day rollover)."""
    with _UPCOMING_EARNINGS_LOCK:
        cached = _UPCOMING_EARNINGS_CACHE.get(ticker)
        if cached is not None and (time.time() - cached[1]) < _UPCOMING_EARNINGS_TTL:
            return cached[0]
    result: Optional[dict] = None
    try:
        tk = yf.Ticker(ticker)
        cal = tk.calendar
        dates = []
        if isinstance(cal, dict):
            raw = cal.get("Earnings Date", [])
            dates = raw if isinstance(raw, list) else [raw]
        elif cal is not None and hasattr(cal, "columns") and "Earnings Date" in cal.columns:
            dates = cal["Earnings Date"].tolist()
        today = datetime.now().date()
        next_date = None
        for d in dates:
            try:
                dt = d.replace(tzinfo=None) if hasattr(d, "tzinfo") else datetime.fromisoformat(str(d))
                dd = dt.date()
            except Exception:
                continue
            if (dd - today).days >= 0 and (next_date is None or dd < next_date):
                next_date = dd
        if next_date is not None:
            last_pct = prior_pct = None
            _, detail = _earnings_score(tk)
            surprises = (detail or {}).get("surprises") or []  # oldest → newest, fractions
            if surprises:
                last_pct = round(surprises[-1] * 100, 2)
                if len(surprises) >= 2:
                    prior_pct = round(surprises[0] * 100, 2)
            result = {
                "earnings_date":      next_date.isoformat(),
                "last_surprise_pct":  last_pct,
                "prior_surprise_pct": prior_pct,
            }
    except Exception as exc:
        logger.warning("%s: upcoming-earnings fetch failed: %s", ticker, exc)
        result = None
    with _UPCOMING_EARNINGS_LOCK:
        _UPCOMING_EARNINGS_CACHE[ticker] = (result, time.time())
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


# Maximum aggregate Kelly-derived exposure across all positions opened in a single
# run, as a fraction of equity. 1.0 → total exposure capped at 100% of equity.
PORTFOLIO_KELLY_CAP = 1.0

# Minimum per-position size as a fraction of equity. A position sized below this
# (including after portfolio normalization scales it down) is not worth opening and
# is dropped from the run. Shared by _position_dollars and the live-job normalization.
POSITION_FLOOR_PCT = 0.005


def _normalize_portfolio_sizing(sizes: dict[str, float], cap: float) -> dict[str, float]:
    """Scale a map of per-position sizes down proportionally so their sum does not
    exceed `cap`, returning the sizes unchanged when already within the cap.

    Shared by the live signal job (sizes are dollar amounts, cap = PORTFOLIO_KELLY_CAP ×
    equity) and the /api/portfolio/sizing endpoint (sizes are Kelly fractions, cap =
    PORTFOLIO_KELLY_CAP) so the two paths can never drift apart.
    """
    total = sum(sizes.values())
    if total > cap and total > 0:
        return {k: v / total * cap for k, v in sizes.items()}
    return dict(sizes)


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
    # C1 fix (Jul 2026): target against ANNUALIZED vol (daily × √252), the same
    # convention as the portfolio backtest and /api/portfolio/sizing. The old daily-vol
    # denominator made vol_weight ~16× too large, so vol-target sizes always slammed
    # into the 10% hard cap and the 3×-vol-target Kelly guard below could never bind.
    ann_vol = daily_vol * math.sqrt(252.0) if daily_vol > 0 else 0.0
    vol_weight = (0.01 / ann_vol) if ann_vol > 0 else 0.05
    # Pre-fix (daily-vol) weight, kept ONLY for the one-cycle [SIZING_MIGRATION_CHECK]
    # comparison logs below; remove together with them once the first post-deploy
    # signal-job run has been reviewed.
    old_vol_weight = (0.01 / daily_vol) if daily_vol > 0 else 0.05

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
    old_vol_base = old_vol_weight * equity * multiplier * perf_multiplier  # migration log only

    # ── Correlation penalty vs currently-held positions ─────────────────────────
    # Mirror /api/portfolio/sizing via the shared _correlation_penalty helper: shrink
    # the size when the candidate moves with an already-open position, so simultaneous
    # BUYs on correlated names don't stack risk the backtest never sized for. Best-effort:
    # any failure (no Alpaca client, network error, insufficient history) logs a warning
    # and proceeds with no penalty — a correlation fetch must never block a trade.
    penalty, max_corr, corr_tkr = 1.0, 0.0, None
    try:
        if _alpaca_client is not None:
            held = [p.symbol for p in _alpaca_client.get_all_positions()
                    if p.symbol.upper() != ticker.upper()]
            if held:
                prices: dict[str, np.ndarray] = {}
                for t in [ticker, *held]:
                    try:
                        prices[t] = fetch_ohlcv(t, days=120, min_bars=61)["Close"].values.astype(float)
                    except Exception:
                        pass  # missing history → helper skips this ticker
                penalty, max_corr, corr_tkr = _correlation_penalty(ticker, held, prices)
    except Exception as e:
        logger.warning(
            "Correlation penalty fetch failed for %s: %s — proceeding without penalty",
            ticker, e,
        )
        penalty, max_corr, corr_tkr = 1.0, 0.0, None

    def _penalize(raw: float) -> float:
        """Apply the correlation penalty to a raw dollar size, logging when it bites."""
        if penalty >= 1.0:
            return raw
        adjusted = raw * penalty
        logger.info(
            "Correlation penalty applied to %s: raw_size=%.0f → adjusted=%.0f "
            "(max_corr=%.2f with %s)",
            ticker, raw, adjusted, max_corr, corr_tkr,
        )
        return adjusted

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
        kelly_raw = kelly_frac * equity * multiplier * perf_multiplier
        # If Kelly is more than 3× vol-target, warn and cap. With the annualized
        # vol-target reference this cap can now genuinely bind (against the old
        # daily-vol reference, ~16× too high, it never did).
        cap_3x = vol_base * 3.0
        cap_binds = kelly_raw > cap_3x
        kelly_dollars = kelly_raw
        if cap_binds:
            logger.warning(
                "%s: Kelly $%.0f exceeds 3× vol-target $%.0f — capping",
                ticker, kelly_raw, cap_3x,
            )
            kelly_dollars = cap_3x
        # Correlation penalty, then hard cap 10%, floor POSITION_FLOOR_PCT
        kelly_dollars = _penalize(kelly_dollars)
        kelly_dollars = max(min(kelly_dollars, equity * 0.10), equity * POSITION_FLOOR_PCT)
        # One-cycle migration check: pre-fix (daily-vol) size alongside the new size so
        # the first post-deploy signal run can be sanity-checked from Railway logs.
        # Remove this block (and old_vol_weight/old_vol_base above) once reviewed.
        old_cap_3x = old_vol_base * 3.0
        old_dollars = max(
            min(min(kelly_raw, old_cap_3x) * penalty, equity * 0.10),
            equity * POSITION_FLOOR_PCT,
        )
        logger.info(
            "[SIZING_MIGRATION_CHECK] %s method=%s old_size=$%.0f new_size=$%.0f "
            "cap3x_binds_old=%s cap3x_binds_new=%s ann_vol=%.4f",
            ticker, sizing_method, old_dollars, kelly_dollars,
            kelly_raw > old_cap_3x, cap_binds, ann_vol,
        )
        return kelly_dollars, round(kelly_frac, 6), sizing_method

    # Vol-targeting fallback
    raw_dollars = max(min(_penalize(vol_base), equity * 0.10), equity * POSITION_FLOOR_PCT)
    # One-cycle migration check — see the note on the Kelly path above.
    old_dollars = max(min(old_vol_base * penalty, equity * 0.10), equity * POSITION_FLOOR_PCT)
    logger.info(
        "[SIZING_MIGRATION_CHECK] %s method=vol_target_fallback old_size=$%.0f "
        "new_size=$%.0f ann_vol=%.4f",
        ticker, old_dollars, raw_dollars, ann_vol,
    )
    return raw_dollars, 0.0, "vol_target_fallback"


def _trading_days_between(start: datetime, end: datetime) -> int:
    try:
        return int(np.busday_count(start.date(), end.date()))
    except Exception:
        return max(0, int((end - start).days * 5 / 7))


def _skip_if_locked(
    ticker: str,
    *,
    composite: Optional[float] = None,
    signal: Optional[str] = None,
    price: Optional[float] = None,
    atr: Optional[float] = None,
) -> bool:
    """If the position is user-locked, log a 'position_locked' skip and return True so
    the caller can bypass the automated close. Manual closes (the UI Close button →
    /api/portfolio/positions/close) call Alpaca directly and never hit this check."""
    if db.is_position_locked(ticker):
        db.log_signal(ticker, composite, signal, "skipped", "position_locked", price, atr)
        logger.warning("%s: automated close skipped — position locked by user", ticker)
        return True
    return False


def _record_close(ticker: str, current_price: float, entry_price: float,
                  exit_reason: str, entry_log: Optional[dict],
                  score: Optional[float] = None) -> None:
    """Write signal_log SELL + trade_outcomes + ticker_performance for a close that has
    already executed at the broker. Full closes arm the re-entry cooldown; a profit-take
    half-close records its own leg without arming it (the position is still open).

    A position that was profit-taken produces TWO trade_outcomes rows against the same
    entry_signal_id: leg='profit_take' (the +15% trimmed half, written here at trim
    time) and leg='remainder' (the other half, written when the rest closes). Each leg
    carries half the original entry_dollars and its own realized return, so Kelly, edge
    stats, ticker_performance and the adaptive-threshold job see the true per-leg
    win/loss distribution — previously the banked +15% half was never recorded and a
    trimmed trade could be logged as a single loss. Untrimmed positions still write
    exactly one row (leg NULL, full entry_dollars).

    Retries transient DB failures (e.g. SQLite locks when the scheduler and a request
    handler hit the DB at once) up to 3 times before giving up, so a momentary lock does
    not permanently lose the trade record."""
    ret = (current_price - entry_price) / entry_price * 100
    hold = 0
    if entry_log:
        try:
            hold = _trading_days_between(
                datetime.fromisoformat(entry_log["timestamp"]), datetime.utcnow()
            )
        except Exception:
            pass

    entry_signal_id = entry_log["id"] if entry_log else None
    score_at_entry  = entry_log.get("composite_score") if entry_log else None
    full_dollars    = entry_log.get("entry_dollars") if entry_log else None

    if exit_reason == "profit_take_half":
        # Trimmed half: its own outcome row, half the dollars, no cooldown (see
        # record_close_transaction, which skips last_exit_at for this exit_reason).
        # return_pct on entry_price is exactly the trimmed half's realized return.
        leg = "profit_take"
        leg_dollars = full_dollars / 2 if full_dollars else None
        log_reason = f"profit_take_half:+{ret:.1f}%"
    else:
        # Full close. If this holding was already trimmed, this row is the remaining
        # half (other half of the dollars); otherwise the whole position, as before.
        trimmed = bool(
            entry_log and db.has_partial_close_since(ticker, entry_log["timestamp"])
        )
        leg = "remainder" if trimmed else None
        leg_dollars = (full_dollars / 2 if trimmed else full_dollars) if full_dollars else None
        log_reason = None  # plain exit_reason in signal_log

    for attempt in range(3):
        try:
            db.record_close_transaction(
                ticker, score, exit_reason, current_price, entry_price,
                ret, hold, entry_signal_id, score_at_entry,
                leg=leg, entry_dollars=leg_dollars, log_reason=log_reason,
            )
            break
        except Exception as db_err:
            if attempt < 2:
                logger.warning(
                    "DB write failed (attempt %d/3) for %s close — retrying: %s",
                    attempt + 1, ticker, db_err,
                )
                time.sleep(1)
            else:
                logger.error(
                    "broker close SUCCEEDED but DB transaction FAILED after 3 attempts "
                    "for %s — trade leg unrecorded: %s",
                    ticker, db_err,
                )
    if exit_reason == "profit_take_half":
        logger.info("%s: profit-take half at %.2f (+%.1f%%)", ticker, current_price, ret)
    else:
        logger.info("%s: closed (%s) at %.2f (%.1f%%)", ticker, exit_reason, current_price, ret)


def _close_and_record(api, ticker: str, current_price: float, entry_price: float,
                      exit_reason: str, entry_log: Optional[dict],
                      score: Optional[float] = None) -> None:
    """Close an Alpaca position and write trade_outcomes."""
    api.close_position(ticker)
    _record_close(ticker, current_price, entry_price, exit_reason, entry_log, score)


# ── Scheduled jobs ────────────────────────────────────────────────────────────

def _write_gate_stats() -> None:
    cutoff = (datetime.utcnow() - timedelta(days=90)).isoformat()
    gate_names = [
        "bull_prob_below_threshold", "hmm_regime_uncertain", "score_below_threshold",
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
    bull_threshold = float(db.get_config("bull_threshold", "63"))
    bear_threshold = float(db.get_config("bear_threshold", "80"))
    buy_threshold  = bear_threshold if not spy_above else bull_threshold
    oe_thresh        = float(db.get_config("OVEREXTENDED_THRESHOLD_PCT", "0.25"))
    _mff_cfg         = db.get_config("MIN_FACTOR_FLOOR", "")
    min_factor_floor = float(_mff_cfg) if _mff_cfg else None
    logger.info("Macro: SPY>200d=%s VIX=%.1f threshold=%.0f",
                spy_above, vix, buy_threshold)

    # Automated-trading toggle. We always compute and log signals so the user can see what
    # the system would have done; the toggle only gates actual order placement.
    #   "all"          → pause everything: no new entries, no automatic exits
    #   "entries_only" → pause new entries only; exits still run
    trading_enabled = db.get_automated_trading_enabled()
    trading_mode    = db.get_automated_trading_mode()
    entries_paused  = not trading_enabled
    exits_paused    = (not trading_enabled) and trading_mode == "all"
    if entries_paused and trading_mode == "all":
        logger.info("Automated trading is paused (all). Signals still computed; "
                    "no entries or exits will be placed.")
    elif entries_paused:
        logger.info("Automated trading is paused (entries only). New entries skipped, "
                    "exits still active.")

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

    with ThreadPoolExecutor(max_workers=2) as pool:
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

    # Sequential gate evaluation (must not be parallelised). Order submission is
    # deferred until after the loop so portfolio-wide Kelly normalization can be
    # applied across all BUY candidates in this run before any order is placed.
    buy_candidates: list[dict] = []
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
            signal_label       = result.get("signal", "HOLD")  # composite-derived BUY/HOLD/SELL, for logging
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

            # Minimum factor coverage before acting on a deterioration SELL: require at
            # least 3 of the 5 factors to have valid (non-null) scores. A composite built
            # from one or two surviving factors is too thin to justify force-closing a
            # position — bad/missing factor data must never trigger a spurious SELL.
            factor_coverage = sum(1 for v in result.get("factors", {}).values() if not v["null"])

            # Score deterioration exit — skip for transition regime (smoothed_bull_prob in [0.35, 0.65])
            if in_pos and composite < 40.0 and hmm_regime != "transition":
                if factor_coverage < 3:
                    db.log_signal(ticker, composite, signal_label, "skipped",
                                  "insufficient_factor_coverage", price, atr,
                                  hmm_regime=hmm_regime, sentiment_score=sentiment,
                                  smoothed_bull_prob=smoothed_bull_prob)
                    logger.info("%s: deterioration SELL skipped — only %d/5 factors available",
                                ticker, factor_coverage)
                    continue
                if _skip_if_locked(ticker, composite=composite, signal=signal_label,
                                   price=price, atr=atr):
                    continue
                # In "all" pause mode, automatic exits are suspended too (entries_only keeps them).
                if exits_paused:
                    db.log_signal(ticker, composite, signal_label, "skipped",
                                  "trading_paused_all", price, atr,
                                  hmm_regime=hmm_regime, sentiment_score=sentiment,
                                  smoothed_bull_prob=smoothed_bull_prob)
                    continue
                pos = positions[ticker]
                entry_log = db.get_last_buy_signal(ticker)
                try:
                    _close_and_record(api, ticker, price, float(pos.avg_entry_price),
                                      "score_deterioration", entry_log, score=composite)
                except Exception as e:
                    db.log_signal(ticker, composite, signal_label, "skipped",
                                  f"close_failed:{e}", price, atr)
                continue

            # Gaussian-HMM regime gate: require ≥70% confidence in a bull regime to enter.
            if smoothed_bull_prob < 0.70:
                db.log_signal(ticker, composite, signal_label, "skipped", "bull_prob_below_threshold", price, atr,
                              hmm_regime=hmm_regime, sentiment_score=sentiment,
                              smoothed_bull_prob=smoothed_bull_prob, hmm_fit_failed=hmm_fit_failed)
                continue

            if effective_composite < buy_threshold:
                db.log_signal(ticker, composite, signal_label, "skipped",
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
            # Gate uses the skip-21 returns (matches the backtest), not the display fields
            ret_3m  = result.get("ret_3m_skip")
            ret_12m = result.get("ret_12m_skip")
            if ret_3m is not None and ret_12m is not None:
                if (ret_3m + ret_12m) <= 0 or ret_3m < -0.10 or ret_12m < -0.10:
                    db.log_signal(ticker, effective_composite, "BUY", "skipped",
                                  "momentum_disagreement", price, atr,
                                  hmm_regime=hmm_regime, sentiment_score=sentiment,
                                  smoothed_bull_prob=smoothed_bull_prob)
                    continue
            # Re-entry cooldown: block re-entry for 2 trading days after non-signal exits
            perf = db.get_ticker_performance(ticker)
            if perf and perf.get("last_exit_at"):
                try:
                    last_exit = datetime.fromisoformat(perf["last_exit_at"])
                    days_since = _trading_days_between(last_exit, datetime.utcnow())
                    if days_since < 2:
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
            # Candidate accepted: reserve its sector slot now (so later same-sector
            # tickers in this run still see the concentration cap) and defer the order
            # to the post-loop normalization + submission pass below.
            #
            # Intentional: the slot is reserved at acceptance, not on confirmed fill. A
            # candidate that later fails at order submission (or is dropped sub-floor) keeps
            # its slot for the rest of this run, which can block a later same-sector
            # candidate. This is by design — we favor conservative sector exposure over
            # maximizing fill count within a single cycle. Not a bug; do not "fix" it by
            # moving the increment to the submission pass.
            open_sector_counts[sector] = open_sector_counts.get(sector, 0) + 1
            buy_candidates.append({
                "ticker": ticker, "dollars": dollars, "kelly_frac": kelly_frac,
                "sizing_method": sizing_method, "price": price, "atr": atr,
                "composite": composite, "effective_composite": effective_composite,
                "hmm_regime": hmm_regime, "sentiment": sentiment,
                "smoothed_bull_prob": smoothed_bull_prob,
                "hmm_fit_failed": hmm_fit_failed, "sector": sector,
                "equity": equity,
            })
        except Exception as e:
            logger.error("Signal job error for %s: %s", ticker, e)

    # Portfolio-wide Kelly normalization with sub-floor dropping. Cap aggregate exposure
    # across all BUYs in this run at PORTFOLIO_KELLY_CAP × equity, scaling each position
    # down proportionally when the sum would exceed it (the per-position 10% cap is already
    # applied inside _position_dollars; both constraints apply, whichever binds first).
    #
    # Proportional scaling can push a position below the POSITION_FLOOR_PCT floor; such
    # positions aren't worth opening, so we drop them and re-normalize the survivors against
    # the same cap — dropping frees headroom the remaining positions should use. This
    # converges in ≤2 iterations (dropping only shrinks the total, so survivors only grow
    # and never re-cross the floor), but we cap the loop and warn so a pathological input
    # can't hang the job. Normalize from each candidate's original size every pass so freed
    # headroom is redistributed rather than lost.
    floor = POSITION_FLOOR_PCT * equity
    raw_dollars = {c["ticker"]: c["dollars"] for c in buy_candidates}
    dropped: list[dict] = []
    converged = False
    for _ in range(5):
        if not buy_candidates:
            converged = True
            break
        size_map = {c["ticker"]: raw_dollars[c["ticker"]] for c in buy_candidates}
        normalized = _normalize_portfolio_sizing(size_map, PORTFOLIO_KELLY_CAP * equity)
        for c in buy_candidates:
            c["dollars"] = normalized[c["ticker"]]
        sub_floor = [c for c in buy_candidates if c["dollars"] < floor]
        if not sub_floor:
            converged = True
            break
        dropped.extend(sub_floor)
        buy_candidates = [c for c in buy_candidates if c["dollars"] >= floor]
    if not converged:
        logger.warning(
            "Portfolio normalization did not converge in 5 iterations; proceeding with "
            "%d candidate(s), dropped %d", len(buy_candidates), len(dropped),
        )

    for c in dropped:
        db.log_signal(
            c["ticker"], c["effective_composite"], "BUY", "skipped",
            "below_floor_after_normalization", c["price"], c["atr"],
            hmm_regime=c["hmm_regime"], sentiment_score=c["sentiment"],
            smoothed_bull_prob=c["smoothed_bull_prob"],
        )
        logger.info("%s: skipped (below %.1f%% floor after portfolio normalization)",
                    c["ticker"], POSITION_FLOOR_PCT * 100)

    for c in buy_candidates:
        ticker, dollars, price, atr = c["ticker"], c["dollars"], c["price"], c["atr"]
        # Automated-trading toggle: skip new entries when paused (both "all" and
        # "entries_only"), but still log them so the feed shows what would have happened.
        if entries_paused:
            reason = "trading_paused_all" if trading_mode == "all" else "trading_paused_entries"
            db.log_signal(
                ticker, c["effective_composite"], "BUY", "skipped", reason, price, atr,
                hmm_regime=c["hmm_regime"], sentiment_score=c["sentiment"],
                smoothed_bull_prob=c["smoothed_bull_prob"],
            )
            logger.info("%s: BUY skipped — automated trading paused (%s)", ticker, trading_mode)
            continue
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
                ticker, c["effective_composite"], "BUY", "ordered", None, price, atr,
                hmm_regime=c["hmm_regime"], sentiment_score=c["sentiment"],
                smoothed_bull_prob=c["smoothed_bull_prob"],
                kelly_fraction=c["kelly_frac"], sizing_method=c["sizing_method"],
                hmm_fit_failed=c["hmm_fit_failed"],
                # c["dollars"] is the actual amount risked (post-normalization + cap/floor);
                # c["equity"] is the account equity used to size it.
                entry_dollars=round(c["dollars"], 2), equity_at_entry=round(c["equity"], 2),
            )
            if atr > 0:
                db.update_trailing_stop(signal_id, price - 1.5 * atr)
            logger.info(
                "%s: BUY $%.0f score=%.1f regime=%s bull_prob=%.2f sizing=%s kelly=%.3f sector=%s",
                ticker, dollars, c["effective_composite"], c["hmm_regime"],
                c["smoothed_bull_prob"], c["sizing_method"], c["kelly_frac"], c["sector"],
            )
        except Exception as e:
            db.log_signal(ticker, c["composite"], "BUY", "skipped",
                          f"order_failed:{e}", price, atr)

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


def _run_equity_sampler_job() -> None:
    """Sample account equity into equity_snapshots so the 1D curve has data even when no
    browser is polling /live-equity. Runs every 5 min during market hours; cheap and quiet."""
    api = _alpaca_client
    if api is None:
        return
    try:
        equity = round(float(api.get_account().equity), 2)
        db.insert_equity_snapshot(equity)
    except Exception as e:
        logger.warning("Equity sampler failed: %s", e)


def _run_equity_prune_job() -> None:
    """Daily: drop equity_snapshots older than 30 days to keep the table bounded."""
    try:
        removed = db.prune_equity_snapshots()
        logger.info("Equity-snapshot prune removed %d old rows", removed)
    except Exception as e:
        logger.warning("Equity-snapshot prune failed: %s", e)


def _run_stoploss_job() -> None:
    logger.info("▶ Stop-loss job starting")
    db.set_config("last_stoploss_job_at", datetime.utcnow().isoformat())

    # Automated-trading toggle: the stop-loss job handles automatic exits (ATR stop,
    # 21-day hold, macro protection). These are paused only in "all" mode; "entries_only"
    # keeps exits running. (New entries are gated separately in _run_signal_job.)
    if not db.get_automated_trading_enabled() and db.get_automated_trading_mode() == "all":
        logger.info("Automated trading is paused (all). Stop-loss job skipped.")
        return

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
                        if _skip_if_locked(pos.symbol, price=float(pos.current_price)):
                            continue
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

            # Profit-take: close half the position once it is up +15% from entry. The
            # remaining half stays open under the trailing-stop / hold logic below. Skip when
            # the position is user-locked, or when we already trimmed this holding — entry_log
            # is the current BUY, so a later re-entry resets the basis and re-arms the trim.
            current_return = (price - entry_price) / entry_price
            if (current_return >= 0.15
                    and not db.is_position_locked(ticker)
                    and not db.has_partial_close_since(ticker, entry_log["timestamp"])):
                try:
                    api.close_position(
                        ticker, close_options=AlpacaClosePositionRequest(percentage="50")
                    )
                    _record_close(ticker, price, entry_price, "profit_take_half",
                                  entry_log, score=None)
                    logger.info("%s: profit-take half at +%.1f%%", ticker, current_return * 100)
                except Exception as e:
                    logger.error("%s: profit-take failed: %s", ticker, e)
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
                if _skip_if_locked(
                    ticker,
                    composite=entry_log.get("composite_score"),
                    price=price,
                    atr=atr_entry,
                ):
                    continue
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

    BULL_MIN, BULL_MAX = 63.0, 80.0
    BEAR_MIN, BEAR_MAX = 75.0, 85.0
    EWA_ALPHA = 0.15  # new = old * 0.85 + target * 0.15
    MIN_TRADES = 5

    bull_old = float(db.get_config("bull_threshold", "63"))
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
    bull_threshold = float(db.get_config("bull_threshold", "63"))
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


# ── Analytics deep-dives ────────────────────────────────────────────────────────
# Additive panels for the Automation → Analytics tab. Each is independently cached
# (different freshness needs) via the tiny TTL helper below; all are read-only.

_ANALYTICS_CACHE: dict[str, tuple[Any, float]] = {}
_ANALYTICS_LOCK = threading.Lock()


def _analytics_cached(key: str, ttl: float, compute):
    """Return a cached value for `key`, recomputing via `compute()` when stale."""
    with _ANALYTICS_LOCK:
        hit = _ANALYTICS_CACHE.get(key)
        if hit is not None and (time.time() - hit[1]) < ttl:
            return hit[0]
    value = compute()
    with _ANALYTICS_LOCK:
        _ANALYTICS_CACHE[key] = (value, time.time())
    return value


def _compute_factor_contribution() -> dict:
    snapshots = db.get_watchlist_snapshots()
    all_acc: dict[str, list[float]] = {}
    act_acc: dict[str, list[float]] = {}
    weight_for: dict[str, float] = {}
    ticker_count = 0
    actionable_count = 0
    for snap in snapshots:
        # snap["factors"] is the full cached factors payload; the per-factor
        # breakdown ({name: {score, weight, null}}) is nested under its "factors" key.
        payload = snap.get("factors")
        factors = payload.get("factors") if isinstance(payload, dict) else None
        if not isinstance(factors, dict):
            continue
        ticker_count += 1
        composite = snap.get("composite_score")
        is_actionable = composite is not None and composite >= 63
        if is_actionable:
            actionable_count += 1
        for name, fd in factors.items():
            if not isinstance(fd, dict) or fd.get("null"):
                continue
            score = fd.get("score")
            if score is None:
                continue
            all_acc.setdefault(name, []).append(float(score))
            weight_for[name] = float(fd.get("weight") or DEFAULT_FACTOR_WEIGHTS.get(name, 0.0))
            if is_actionable:
                act_acc.setdefault(name, []).append(float(score))
    out = []
    for name, scores in all_acc.items():
        act = act_acc.get(name, [])
        out.append({
            "name":   name,
            "weight": round(weight_for.get(name, 0.0), 4),
            "avg_score_all":        round(sum(scores) / len(scores), 2),
            "avg_score_actionable": round(sum(act) / len(act), 2) if act else None,
        })
    out.sort(key=lambda x: x["avg_score_all"])
    return {"factors": out, "ticker_count": ticker_count, "actionable_count": actionable_count}


@app.get("/api/analytics/factor-contribution")
def get_factor_contribution():
    """Average factor scores across the current watchlist snapshot, plus the
    actionable subset (composite >= 63). Sorted lowest avg_score_all first so the
    factors dragging composites down surface at the top. Cached 5 minutes."""
    return _analytics_cached("factor_contribution", 300, _compute_factor_contribution)


def _compute_gate_rejections() -> dict:
    since = (datetime.utcnow() - timedelta(days=30)).isoformat()
    stats = db.get_gate_rejection_stats(since)
    total_skipped = sum(r["cnt"] for r in stats["skipped"])
    # skip_reasons carry a variable suffix (e.g. "score_below_threshold:51.9<80");
    # collapse to the gate name before the colon so each gate is one bucket.
    by_gate: dict[str, int] = {}
    for r in stats["skipped"]:
        gate = (r["skip_reason"] or "unknown").split(":", 1)[0]
        by_gate[gate] = by_gate.get(gate, 0) + r["cnt"]
    rejections = [
        {
            "gate":  gate,
            "count": count,
            "pct_of_skipped": round(count / total_skipped * 100, 1) if total_skipped else 0.0,
        }
        for gate, count in sorted(by_gate.items(), key=lambda kv: kv[1], reverse=True)
    ]
    return {
        "period_days":     30,
        "total_evaluated": stats["total_evaluated"],
        "total_skipped":   total_skipped,
        "rejections":      rejections,
    }


@app.get("/api/analytics/gate-rejections")
def get_gate_rejections():
    """Which gates blocked the most live signals over the last 30 days, from
    signal_log action='skipped' rows grouped by skip_reason. Cached 10 minutes."""
    return _analytics_cached("gate_rejections", 600, _compute_gate_rejections)


def _compute_drawdown() -> dict:
    peak, snapshot_count = db.get_equity_peak_and_count()
    current: Optional[float] = None
    if _alpaca_client:
        try:
            current = round(float(_alpaca_client.get_account().equity), 2)
        except Exception:
            pass
    drawdown_pct: Optional[float] = None
    if peak and current is not None and peak > 0:
        drawdown_pct = round((current - peak) / peak * 100, 2)
    return {
        "peak":           round(peak, 2) if peak is not None else None,
        "current":        current,
        "drawdown_pct":   drawdown_pct,
        "snapshot_count": snapshot_count,
    }


@app.get("/api/analytics/drawdown")
def get_drawdown():
    """Current live-account drawdown from all-time peak equity (equity_snapshots
    MAX vs the live Alpaca equity). Cached 60 seconds."""
    return _analytics_cached("drawdown", 60, _compute_drawdown)


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


@app.get("/api/earnings/upcoming")
def api_upcoming_earnings(days: int = 30):
    """Watchlist tickers with earnings in the next `days` calendar days (default 30,
    clamped to 7–90), sorted by date ascending. Per-ticker calendar/surprise fetches run
    in parallel (≤2 workers) and are cached 1h; tickers with no calendar data are skipped.
    composite_score comes from the cached watchlist snapshot (no extra compute)."""
    days = max(7, min(90, days))
    watchlist = [r["ticker"] for r in db.get_watchlist()]
    snap_scores = {s["ticker"]: s.get("composite_score") for s in db.get_watchlist_snapshots()}

    raw: dict[str, Optional[dict]] = {}
    if watchlist:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = {pool.submit(_upcoming_earnings_for_ticker, t): t for t in watchlist}
            for fut in as_completed(futures):
                t = futures[fut]
                try:
                    raw[t] = fut.result()
                except Exception:
                    raw[t] = None

    today = datetime.now().date()
    earnings: list[dict] = []
    for ticker in watchlist:
        info = raw.get(ticker)
        if not info or not info.get("earnings_date"):
            continue
        try:
            ed = datetime.fromisoformat(info["earnings_date"]).date()
        except Exception:
            continue
        days_until = (ed - today).days
        if days_until < 0 or days_until > days:
            continue
        earnings.append({
            "ticker":             ticker,
            "company_name":       _get_company_info(ticker).get("name"),
            "earnings_date":      info["earnings_date"],
            "days_until":         days_until,
            "last_surprise_pct":  info.get("last_surprise_pct"),
            "prior_surprise_pct": info.get("prior_surprise_pct"),
            "composite_score":    snap_scores.get(ticker),
        })
    earnings.sort(key=lambda e: e["earnings_date"])
    return {"days": days, "earnings": earnings}


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


@app.get("/api/paper/sector-exposure")
def api_paper_sector_exposure():
    """Sector breakdown of currently open Alpaca positions.

    Read-only: reuses the cached _get_sector lookup (same data the concentration gate
    uses) to bucket open positions per sector. Each sector's weight is its slot usage —
    open positions in the sector divided by MAX_SECTOR_POSITIONS — so the panel mirrors
    the concentration cap, not dollar exposure. No new sector fetching logic, no scoring,
    no sizing.
    """
    if _alpaca_client is None:
        return {"available": False, "error": _alpaca_err}
    try:
        positions = _alpaca_client.get_all_positions()
    except Exception as e:
        return {"available": False, "error": str(e)}

    max_per_sector = int(db.get_config("MAX_SECTOR_POSITIONS", "3"))
    # Bucket per sector, tracking just the ticker list (slot usage, not dollars).
    buckets: dict[str, list[str]] = {}
    for pos in positions:
        sector = _get_sector(pos.symbol)
        buckets.setdefault(sector, []).append(pos.symbol)

    total = sum(len(tickers) for tickers in buckets.values())
    sectors = sorted(
        (
            {
                "sector": sec,
                "count": len(tickers),
                "tickers": sorted(tickers),
                # Slot usage: open positions / cap, capped at 100%.
                "pct": round(min(len(tickers) / max_per_sector, 1.0) * 100, 1) if max_per_sector else 0.0,
                "at_cap": len(tickers) >= max_per_sector,
                "near_cap": len(tickers) == max_per_sector - 1,
            }
            for sec, tickers in buckets.items()
        ),
        key=lambda x: (-x["count"], x["sector"]),
    )
    return {
        "available": True,
        "max_per_sector": max_per_sector,
        "total_positions": total,
        "sectors": sectors,
    }


def _reconstruct_equity_history(days: int, current_equity: Optional[float]) -> list[dict]:
    """Approximate daily equity curve from closed-trade timestamps + current equity.

    Fallback used only when Alpaca's own portfolio history is unavailable. Read-only:
    walks backward from today's equity, undoing each closed trade's realised return
    (scaled by an assumed average position weight). Clearly approximate — no live calc.
    """
    if current_equity is None:
        return []
    cutoff = datetime.utcnow() - timedelta(days=days)
    trades = [t for t in db.get_trade_history()
              if t.get("exit_timestamp") and t["exit_timestamp"] >= cutoff.isoformat()]
    # Map each day → summed realised return fraction (return_pct is a percent).
    AVG_WEIGHT = 0.10  # rough average position weight; magnitude is illustrative only
    by_day: dict[str, float] = {}
    for t in trades:
        day = t["exit_timestamp"][:10]
        by_day[day] = by_day.get(day, 0.0) + (float(t.get("return_pct") or 0.0) / 100.0) * AVG_WEIGHT
    # Walk backward from today's equity.
    points: list[dict] = []
    equity = float(current_equity)
    today = datetime.utcnow().date()
    series: list[tuple[str, float]] = []
    for i in range(days + 1):
        d = today - timedelta(days=i)
        series.append((d.isoformat(), equity))
        # Undo trades that closed on this day to get the prior day's equity.
        frac = by_day.get(d.isoformat(), 0.0)
        if 1 + frac != 0:
            equity = equity / (1 + frac)
    for date, eq in reversed(series):
        points.append({"date": date, "equity": round(eq, 2)})
    return points


@app.get("/api/paper/equity-history")
def api_paper_equity_history(days: int = 30):
    """Daily account equity over the last `days`. Read-only.

    Prefers Alpaca's own portfolio history (real equity, computed by Alpaca — we only
    read it); falls back to an approximate reconstruction from closed-trade timestamps
    and current equity. No new live calculation of any score or signal.
    """
    days = max(5, min(int(days), 365))
    if _alpaca_client is None:
        return {"available": False, "error": _alpaca_err}

    # Primary: Alpaca portfolio history.
    try:
        req = GetPortfolioHistoryRequest(period=f"{days}D", timeframe="1D")
        hist = _alpaca_client.get_portfolio_history(req)
        ts = getattr(hist, "timestamp", None) or []
        eq = getattr(hist, "equity", None) or []
        points = [
            {"date": datetime.utcfromtimestamp(int(t)).strftime("%Y-%m-%d"),
             "equity": round(float(e), 2)}
            for t, e in zip(ts, eq) if e is not None
        ]
        if points:
            return {"available": True, "source": "alpaca", "approximate": False, "points": points}
    except Exception as e:
        logger.info("Portfolio history unavailable, reconstructing: %s", e)

    # Fallback: approximate reconstruction.
    try:
        acc = _alpaca_client.get_account()
        equity = float(acc.equity)
    except Exception as e:
        return {"available": False, "error": str(e)}
    points = _reconstruct_equity_history(days, equity)
    if not points:
        return {"available": False, "error": "no equity history"}
    return {"available": True, "source": "reconstructed", "approximate": True, "points": points}


@app.get("/api/portfolio/history")
def api_portfolio_history(period: str = "1D"):
    """Account equity curve for the /portfolio page. Read-only, cached per period.

    `period` ∈ {1D,1W,1M,3M,YTD,1Y,Max}. 1D is a rolling now-24h→now window from Alpaca
    (1Min bars, extended-hours reporting — matches Alpaca's own dashboard), with our
    equity_snapshots as a fallback. 1W is likewise a rolling now-7d→now window from Alpaca
    (1Hour bars, extended-hours reporting). Every other period comes from Alpaca's
    portfolio-history endpoint at 1D bars — 1M/3M by Alpaca period, YTD from Jan 1, and
    1Y/Max from the account creation date (both show the full account history).
    Returns {available, period, points: [{timestamp: ISO string, equity: number}]}.
    """
    if period == "all":  # legacy alias from older frontends
        period = "Max"
    if period not in _HISTORY_PERIODS:
        period = "1D"

    cached, ts = _PORTFOLIO_HISTORY_CACHE.get(period, (None, 0.0))
    if cached is not None and (time.time() - ts) < _history_ttl(period):
        return cached

    # 1D: rolling 24h window matching Alpaca's own dashboard — a true now-24h→now span at
    # 1-minute bars with extended_hours reporting. This yields pre/post-market activity and
    # a single overnight gap (post-market close ~00:00 UTC → pre-market open ~08:00 UTC) that
    # the chart renders as the flat overnight line. Our equity_snapshots DB is the fallback
    # if Alpaca is unavailable or returns too few points.
    if period == "1D":
        points: list[dict] = []
        if _alpaca_client is not None:
            try:
                now = datetime.now(timezone.utc)
                req = GetPortfolioHistoryRequest(
                    start=now - timedelta(hours=24),
                    end=now,
                    timeframe="1Min",
                    extended_hours=True,
                    intraday_reporting="extended_hours",
                )
                hist = _alpaca_client.get_portfolio_history(req)
                tstamps  = getattr(hist, "timestamp", None) or []
                equities = getattr(hist, "equity", None) or []
                for t, e in zip(tstamps, equities):
                    if e is None:
                        continue
                    eq = float(e)
                    if eq <= 0:
                        continue
                    points.append({"timestamp": datetime.utcfromtimestamp(int(t)).isoformat() + "Z",
                                   "equity": round(eq, 2)})
            except Exception:
                points = []  # fall through to the equity_snapshots fallback below

        # Fallback: rebuild from our own rolling-24h snapshots when Alpaca gave us nothing.
        if not points:
            try:
                snaps = db.get_equity_snapshots(int(time.time() - 24 * 3600))
            except Exception as e:
                return {"available": False, "error": str(e), "period": period}
            points = [
                {"timestamp": datetime.utcfromtimestamp(int(s["ts"])).isoformat() + "Z",
                 "equity": round(float(s["equity"]), 2)}
                for s in snaps if s["equity"] is not None and float(s["equity"]) > 0
            ]

        result = {"available": True, "period": period, "points": points}
        _PORTFOLIO_HISTORY_CACHE[period] = (result, time.time())
        return result

    if _alpaca_client is None:
        return {"available": False, "error": _alpaca_err, "period": period}

    created_at = _get_account_created_at()

    try:
        if period == "1W":
            # Rolling 7-day window at hourly bars with extended-hours reporting — same
            # pattern as the 1D fix. Gives pre/post-market each day plus the overnight
            # gaps that render as flat lines, matching Alpaca's dashboard.
            now = datetime.now(timezone.utc)
            req = GetPortfolioHistoryRequest(
                start=now - timedelta(days=7),
                end=now,
                timeframe="1H",  # portfolio-history's hourly bar (not "1Hour", which it rejects)
                extended_hours=True,
                intraday_reporting="extended_hours",
            )
        elif period == "YTD":
            # Jan 1 of the current year → now, both tz-aware. The explicit `end` matters:
            # given only a `start` that predates account creation, Alpaca returns a short
            # all-zero placeholder series (which the eq<=0 / pre-creation filters then drop
            # entirely, leaving the chart empty). Passing end=now yields the full daily
            # series including the real post-creation equity.
            now = datetime.now(timezone.utc)
            start = datetime(now.year, 1, 1, tzinfo=timezone.utc)
            req = GetPortfolioHistoryRequest(start=start, end=now, timeframe="1D")
        elif period in ("1Y", "Max"):
            # Both show the full account history: account creation → now (the account is
            # <1 year old, so 1Y and Max are the same window). Explicit end=now for the same
            # reason as YTD — a lone start lets Alpaca return an all-zero placeholder series.
            # Fall back to a long period if the creation date couldn't be fetched (so the
            # chart still loads).
            now = datetime.now(timezone.utc)
            req = (GetPortfolioHistoryRequest(start=created_at, end=now, timeframe="1D")
                   if created_at is not None
                   else GetPortfolioHistoryRequest(period="1A", timeframe="1D"))
        else:  # 1M, 3M — explicit rolling start/end window at daily bars
            now = datetime.now(timezone.utc)
            start = now - timedelta(days=30 if period == "1M" else 90)
            req = GetPortfolioHistoryRequest(start=start, end=now, timeframe="1D")

        hist = _alpaca_client.get_portfolio_history(req)
        tstamps  = getattr(hist, "timestamp", None) or []
        equities = getattr(hist, "equity", None) or []

        # Clean: drop non-positive equity and any point predating the account (Alpaca
        # backfills placeholder points for ranges before the account existed).
        points: list[dict] = []
        for t, e in zip(tstamps, equities):
            if e is None:
                continue
            eq = float(e)
            if eq <= 0:
                continue
            dt = datetime.utcfromtimestamp(int(t))
            if created_at is not None and dt < created_at:
                continue
            points.append({"timestamp": dt.isoformat() + "Z", "equity": round(eq, 2)})

        result = {"available": True, "period": period, "points": points}
        _PORTFOLIO_HISTORY_CACHE[period] = (result, time.time())
        return result
    except Exception as e:
        return {"available": False, "error": str(e), "period": period}


@app.get("/api/portfolio/live-equity")
def api_portfolio_live_equity():
    """Current account equity + timestamp, for the 1D equity curve's live tip.

    Deliberately uncached and tiny: the chart fetches its full history once, then polls this
    every ~10s during market hours to extend only the line's tip (see EquityCurve.tsx).
    Returns {equity, timestamp} where timestamp is a UTC ISO string.
    """
    if _alpaca_client is None:
        return {"available": False, "error": _alpaca_err}
    try:
        equity = round(float(_alpaca_client.get_account().equity), 2)
        # Persist the sample so the 1D curve can be rebuilt as a true rolling-24h window
        # (Alpaca's intraday history only covers the current session). Best-effort: a DB
        # write failure must not break the live tip the chart depends on.
        try:
            db.insert_equity_snapshot(equity)
        except Exception:
            pass
        return {"equity": equity, "timestamp": datetime.utcnow().isoformat() + "Z"}
    except Exception as e:
        return {"available": False, "error": str(e)}


@app.get("/api/portfolio/positions/entry-signals")
def api_portfolio_entry_signals():
    """Entry data for currently-open positions: {ticker: {entry_score, entry_date, entry_price}}.

    For each open Alpaca position, looks up the most recent BUY/ordered row in signal_log —
    the entry that opened the still-open position (no subsequent SELL, since the position is
    still held). Read-only. Returns {available, entries}.
    """
    if _alpaca_client is None:
        return {"available": False, "error": _alpaca_err}
    try:
        positions = _alpaca_client.get_all_positions()
    except Exception as e:
        return {"available": False, "error": str(e)}

    entries: dict[str, dict] = {}
    for pos in positions:
        buy = db.get_last_buy_signal(pos.symbol)
        if not buy:
            continue
        entries[pos.symbol] = {
            "entry_score": buy.get("composite_score"),
            "entry_date":  buy.get("timestamp"),
            "entry_price": buy.get("price_at_signal"),
        }
    return {"available": True, "entries": entries}


class ClosePositionRequest(BaseModel):
    ticker: str
    qty: float


@app.post("/api/portfolio/positions/close")
def api_portfolio_close_position(req: ClosePositionRequest):
    """Submit a market sell order to close (all or part of) an open position.

    Returns {success, order_id} on success or {success: false, error} on failure.
    """
    if _alpaca_client is None:
        return {"success": False, "error": _alpaca_err}
    ticker = req.ticker.upper()
    qty = round(req.qty, 6)
    try:
        # Capture entry/current price before submitting so we can record the close.
        pos = None
        try:
            for p in _alpaca_client.get_all_positions():
                if p.symbol == ticker:
                    pos = p
                    break
        except Exception:
            pos = None

        order = _alpaca_client.submit_order(MarketOrderRequest(
            symbol=ticker,
            qty=qty,
            side=OrderSide.SELL,
            time_in_force=TimeInForce.DAY,
        ))

        # Record the close so manual exits match automated ones (signal_log SELL +
        # trade_outcomes + ticker_performance, arming the re-entry cooldown). Only a
        # full close is recorded — a partial sell leaves the position open, so writing
        # a close transaction and arming the cooldown would be wrong.
        if pos is not None:
            try:
                pos_qty = abs(float(pos.qty))
                if qty >= pos_qty - 1e-6:
                    _record_close(
                        ticker,
                        float(pos.current_price),
                        float(pos.avg_entry_price),
                        "manual_close",
                        db.get_last_buy_signal(ticker),
                    )
            except Exception as rec_err:
                logger.error("%s: manual close order OK but recording failed: %s",
                             ticker, rec_err)
        return {"success": True, "order_id": str(order.id)}
    except Exception as e:
        return {"success": False, "error": str(e)}


class LockPositionRequest(BaseModel):
    ticker: str


@app.post("/api/portfolio/positions/lock")
def api_portfolio_lock_position(req: LockPositionRequest):
    """Lock a position so automated exits (stop loss, 21-day hold, macro protection,
    score deterioration) skip it. The Close button still works manually."""
    ticker = req.ticker.upper()
    locked_at = db.lock_position(ticker)
    logger.info("%s: position locked by user", ticker)
    return {"success": True, "ticker": ticker, "locked_at": locked_at}


@app.post("/api/portfolio/positions/unlock")
def api_portfolio_unlock_position(req: LockPositionRequest):
    """Unlock a position, restoring automated exit handling."""
    ticker = req.ticker.upper()
    db.unlock_position(ticker)
    logger.info("%s: position unlocked by user", ticker)
    return {"success": True, "ticker": ticker}


@app.get("/api/portfolio/positions/locked")
def api_portfolio_locked_positions():
    """List of currently user-locked tickers."""
    return {"locked": db.get_locked_positions()}


class TradingSettingsRequest(BaseModel):
    automated_trading_enabled: Optional[bool] = None
    automated_trading_mode: Optional[str] = None


def _trading_settings_payload() -> dict:
    return {
        "automated_trading_enabled": db.get_automated_trading_enabled(),
        "automated_trading_mode":    db.get_automated_trading_mode(),
    }


@app.get("/api/settings/trading")
def api_get_trading_settings():
    """Current automated-trading toggle state (persisted in system_config)."""
    return _trading_settings_payload()


@app.post("/api/settings/trading")
def api_set_trading_settings(req: TradingSettingsRequest):
    """Update whichever toggle fields are provided; returns the full updated settings."""
    if req.automated_trading_enabled is not None:
        db.set_automated_trading_enabled(req.automated_trading_enabled)
    if req.automated_trading_mode is not None:
        if req.automated_trading_mode not in db.TRADING_MODES:
            raise HTTPException(
                status_code=422,
                detail=f"automated_trading_mode must be one of {db.TRADING_MODES}",
            )
        db.set_automated_trading_mode(req.automated_trading_mode)
    settings = _trading_settings_payload()
    logger.info("Trading settings updated: %s", settings)
    return settings


@app.get("/api/portfolio/edge-stats")
def api_portfolio_edge_stats():
    """Aggregate expectancy across all closed trades (signal_log JOIN trade_outcomes).

    Read-only; cached 10 min. Returns n, win_rate (%), avg win/loss %, expectancy %
    per trade and avg holding days. Adds low_sample=true when there are <10 closed
    trades (too few to draw conclusions from).
    """
    global _EDGE_STATS_CACHE
    if _EDGE_STATS_CACHE is not None:
        cached, ts = _EDGE_STATS_CACHE
        if (time.time() - ts) < _EDGE_STATS_TTL:
            return cached

    stats = db.get_edge_stats()
    if stats["n"] < 10:
        stats["low_sample"] = True
    _EDGE_STATS_CACHE = (stats, time.time())
    return stats


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
    ("bull_prob_below_threshold",  "HMM regime supports buying"),
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
    if key == "bull_prob_below_threshold":
        regime_txt = regime or "unknown"
        if status == "failed":
            return f"Regime {regime_txt} — Gaussian HMM bull probability {_pct(bull_prob)} below the 70% bar"
        return f"Regime {regime_txt}, Gaussian HMM bull probability {_pct(bull_prob)} ≥ 70%"
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
        return ("Within the 2-day cooldown after a recent non-signal exit"
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

    ctx = {
        "threshold": None,
    }
    try:
        # Display context only: the thresholds the gate uses (read from config, not recomputed).
        bull_thr = float(db.get_config("bull_threshold", "63"))
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


_SKIP_LABELS: dict[str, str] = {
    "earnings_within_2d":     "Earnings within 2 days",
    "data_unavailable":       "Data unavailable",
    "bull_prob_below_threshold": "HMM regime not bullish",
    # Retained for historical rows logged before the rename; no new rows carry it.
    "hmm_not_buy_transition": "HMM regime not bullish (historical)",
    "score_below_threshold":  "Score below threshold",
    "sentiment_too_low":      "Sentiment too negative",
    "vix_too_high":           "VIX too high",
    "already_in_position":    "Already held",
    "volume_below_average":   "Volume not confirmed",
    "overextended":           "Overextended",
    "momentum_disagreement":  "Momentum disagreement",
    "reentry_cooldown":       "Re-entry cooldown",
    "sector_concentration":   "Sector at concentration cap",
    "order_failed":           "Order submission failed",
    "close_failed":           "Position close failed",
}


def _skip_key(reason: Optional[str]) -> str:
    if not reason:
        return "other"
    return reason.split(":", 1)[0]


def _build_briefing() -> dict:
    rows = db.get_latest_run_rows()
    if not rows:
        return {
            "available": False,
            "run_at": None,
            "summary_data": {},
            "evaluated_count": 0,
            "orders": [], "skip_breakdown": [], "near_misses": [],
            "macro_flags": [], "positions_closed": 0,
            "account": {"available": False},
        }

    eval_rows = [r for r in rows if r.get("action") != "closed"]
    closed_rows = [r for r in rows if r.get("action") == "closed"]
    run_at = max((r.get("timestamp") or "") for r in eval_rows) if eval_rows else (rows[-1].get("timestamp"))

    evaluated = sorted({r["ticker"] for r in eval_rows})

    orders = [
        {
            "ticker": r["ticker"],
            "price": r.get("price_at_signal"),
            "score": r.get("composite_score"),
            "sizing_method": r.get("sizing_method"),
        }
        for r in eval_rows if r.get("action") == "ordered"
    ]

    # Skip reason counts (one entry per skipped ticker).
    skip_counts: dict[str, int] = {}
    for r in eval_rows:
        if r.get("action") != "skipped":
            continue
        key = _skip_key(r.get("skip_reason"))
        skip_counts[key] = skip_counts.get(key, 0) + 1
    skip_breakdown = sorted(
        ({"key": k, "label": _SKIP_LABELS.get(k, k.replace("_", " ").title()), "count": v}
         for k, v in skip_counts.items()),
        key=lambda x: x["count"], reverse=True,
    )

    # Near-misses: score-gated tickers within 5 points of the threshold (parsed from the
    # value the gate already logged — no recomputation).
    near_misses = []
    for r in eval_rows:
        reason = r.get("skip_reason") or ""
        if not reason.startswith("score_below_threshold:"):
            continue
        try:
            payload = reason.split(":", 1)[1]
            eff_s, thr_s = payload.split("<")
            eff, thr = float(eff_s), float(thr_s)
            gap = thr - eff
            if 0 <= gap <= 5:
                near_misses.append({
                    "ticker": r["ticker"], "score": round(eff, 1),
                    "threshold": round(thr, 0), "gap": round(gap, 1),
                })
        except Exception:
            continue
    near_misses.sort(key=lambda x: x["gap"])

    macro_flags = []
    if any(_skip_key(r.get("skip_reason")) == "vix_too_high" for r in eval_rows):
        macro_flags.append("Volatility circuit breaker active — VIX above threshold, entries paused")

    account = {"available": False}
    if _alpaca_client is not None:
        try:
            acc = _alpaca_client.get_account()
            equity = round(float(acc.equity), 2)
            account = {"available": True, "equity": equity}
            last_equity = getattr(acc, "last_equity", None)
            if last_equity is not None:
                le = float(last_equity)
                if le:
                    account["equity_change_pct"] = round((equity - le) / le * 100, 2)
        except Exception as e:
            account = {"available": False, "error": str(e)}

    return {
        "available": True,
        "run_at": run_at,
        "evaluated_count": len(evaluated),
        "evaluated_tickers": evaluated,
        "orders": orders,
        "skip_breakdown": skip_breakdown,
        "near_misses": near_misses,
        "macro_flags": macro_flags,
        "positions_closed": len(closed_rows),
        "account": account,
    }


@app.get("/api/briefing")
def api_briefing():
    """Read-only aggregation of the most recent signal-job run into a daily briefing.

    Counts skip reasons, lists placed orders and near-miss tickers, and reads account
    equity — all from already-logged signal_log rows / Alpaca. No scoring or signals run.
    """
    return _build_briefing()


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
