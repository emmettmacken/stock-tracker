# Stock Signal Tracker

Kalman-filtered Gaussian-HMM regime detection, five-factor composite scoring, macro-regime-adaptive buy thresholds, fractional Kelly position sizing, alternative data (news sentiment, EDGAR insider filings), walk-forward backtesting, and automated paper trading via Alpaca.

The system's signal is the **composite multi-factor score** (served by `/api/factors`). The discrete per-stock Markov chain that earlier drove `/api/signal` has been removed; that endpoint now returns only the HMM bull/bear regime label.

## Stack

- **Backend**: Python 3.12 · FastAPI · yfinance · hmmlearn · statsmodels · scipy · APScheduler · SQLite · Alpaca SDK (port 8000)
- **Frontend**: Next.js 14 (App Router) · TypeScript · Tailwind CSS v3 · Recharts (port 3000), password-gated via middleware

---

## Quick Start (local)

### 1. Backend

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env             # then fill in your API keys
uvicorn main:app --reload --port 8000
```

### 2. Frontend (separate terminal)

```bash
cd frontend
npm install
npm run dev
```

Open `http://localhost:3000`. The frontend is password-gated — set `LOGIN_PASSWORD` and `SESSION_SECRET` (see below) and log in at `/login`.

Pages: `/` (watchlist), `/stock/[ticker]` (factor breakdown + backtest), `/portfolio` (equity curve + positions), `/strategy-lab` (multi-ticker sizing/backtest), `/automation` (paper-trading controls).

---

## Environment Variables

### Backend (`backend/.env`)

| Variable | Required | Description |
|---|---|---|
| `ALPACA_API_KEY` | Yes | Alpaca paper trading key (`PKxxxxxxxx`) |
| `ALPACA_SECRET_KEY` | Yes | Alpaca secret |
| `ALPACA_BASE_URL` | Yes | `https://paper-api.alpaca.markets/v2` |
| `ALPHA_VANTAGE_KEY` | Recommended | News sentiment scores (free tier: 5 req/min) |
| `DB_PATH` | Optional | SQLite path (default: `backend/stock_tracker.db`) |
| `CORS_ORIGINS` | Optional | Comma-separated frontend origins for production |

### Frontend (`frontend/.env.local`)

| Variable | Required | Description |
|---|---|---|
| `NEXT_PUBLIC_API_URL` | Yes (prod) | Backend base URL (default `http://localhost:8000`) |
| `LOGIN_PASSWORD` | Yes | Password checked by `/api/auth/login` |
| `SESSION_SECRET` | Yes | Opaque value stored in the `session` cookie; middleware compares against it |

---

## Railway Deployment (persistent backend)

Railway keeps the backend alive 24/7 so APScheduler jobs fire on schedule even when your laptop is off.

### Steps

1. **Push your code** to a GitHub repo (the `backend/` folder is the service root).

2. **Create a Railway project** → New Service → GitHub Repo → select your repo.

3. **Set the root directory** to `backend/` (Settings → Source → Root Directory).

4. **Add environment variables** (Settings → Variables) — see backend table above.

5. **Add a persistent Volume** so the SQLite database survives redeploys:
   - Railway dashboard → your service → Volumes → Add Volume
   - Mount path: `/data`
   - Set `DB_PATH=/data/stock_tracker.db` in env vars.

6. **Deploy** — Railway detects `Procfile` and runs:
   ```
   uvicorn main:app --host 0.0.0.0 --port $PORT --workers 1
   ```
   `--workers 1` is required — multiple workers each start their own APScheduler, causing duplicate job firings.

7. **Point the frontend** at the Railway URL via `NEXT_PUBLIC_API_URL`.

---

## Alpaca Paper Trading Setup

1. Sign up at **https://alpaca.markets** → Paper Trading → API Keys → generate a key pair.
2. Add keys to `backend/.env` and restart the backend.

The system runs three scheduled jobs (all times ET):

| Time | Days | Job | What it does |
|---|---|---|---|
| 09:35 | Mon–Fri | Stop-loss check | Closes positions below their trailing ATR stop or held > 21 trading days; closes **all** positions if SPY fell > 3% over the last 5 trading days (`macro_drawdown_protection`) |
| 15:30 | Mon–Fri | Signal job | Pre-fetches sentiment sequentially, computes all factors in parallel, evaluates each ticker through the gate stack, applies portfolio-wide Kelly normalization, then places/closes Alpaca paper orders |
| 18:00 | Sunday | Adaptive thresholds | Recomputes bull/bear buy thresholds via EWA on the last 50 closed trades per regime |

To test without waiting for market hours: **"Run signals now"** on the Automation page, or `POST /api/paper/run-now`.

---

## Signal Pipeline

Each run of the signal job evaluates every watchlist ticker through a sequential gate stack. A ticker must pass every gate to become a buy candidate; candidates are then sized and portfolio-normalized before any order is placed.

### Gate stack (in order)

| Gate | Skip reason | Condition |
|---|---|---|
| Earnings proximity | `earnings_within_2d` | Earnings announcement within 2 calendar days |
| Data availability | `data_unavailable` | yfinance / factor computation failed to return data |
| HMM regime | `bull_prob_below_threshold` | Kalman-smoothed bull probability < 0.70 |
| Composite score | `score_below_threshold:X<Y` | Composite score below the active (macro-aware) buy threshold |
| Sentiment | `sentiment_too_low:X` | Sentiment score < 35 (bearish news flow) |
| VIX | `vix_too_high:X` | VIX > 30 |
| Already held | `already_in_position` | Ticker already in open positions |
| Volume | `volume_below_average` | Last complete day's volume ≤ 1.05 × 20-day average |
| Overextension | `overextended` | Price > MA20 × 1.25 and 63-day return not in the top quartile |
| Momentum | `momentum_disagreement` | 3m + 12m return ≤ 0, or either < −10% |
| Re-entry cooldown | `reentry_cooldown` | Closed within the last 2 trading days |
| Sector cap | `sector_concentration` | Sector already has ≥ 3 open positions |
| Portfolio normalization | `below_floor_after_normalization` | Position scaled below the 0.5%-of-equity floor after aggregate Kelly capping |

### Exit conditions

| Exit reason | Trigger | Where |
|---|---|---|
| `score_deterioration` | Composite score < 40 (suppressed in `transition` regime) | Signal job |
| `stop_loss` | Price falls below trailing ATR stop (entry − 1.5 × ATR, raised as price rises) | Stop-loss job |
| `max_hold_exit` | Position held > 21 trading days | Stop-loss job |
| `macro_drawdown_protection` | SPY fell > 3% over the last 5 trading days — all positions closed | Stop-loss job |

The discrete-Markov `sell_signal` exit has been removed; score deterioration is the sole composite-driven exit.

---

## Factor Engine

The composite score is a weighted average of five factors (0–100 each). Null factors (data unavailable) are dropped and the remaining weights renormalise automatically.

| Factor | Default weight | Source | What it measures |
|---|---|---|---|
| `momentum` | 33.1% | yfinance | 3m + 12m return, each z-scored against its own rolling history (21-day skip) |
| `vol_trend` | 26.5% | yfinance | Price alignment with MA20/MA50/MA200, vol-adjusted |
| `earnings` | 20.8% | yfinance earnings history | EPS surprise direction and magnitude (recent quarters) |
| `sentiment` | 12.9% | Alpha Vantage NEWS_SENTIMENT | Average article sentiment mapped to 0–100 |
| `insider` | 6.6% | SEC EDGAR Form 4 (structured submissions API) | Net open-market purchases vs sales, role-weighted, 30-day lookback |

Factor weights can be overridden per ticker via `factor_weight_overrides.json`; drift > 0.05 from the default is logged to `weight_overrides.json` and exposed at `/api/weight-overrides`.

An optional `MIN_FACTOR_FLOOR` config caps a ticker's entry score (to just below the buy threshold) when any single factor falls below the floor.

A separate `_composite_signal` mapping drives the display-only BUY/HOLD/SELL badge on the watchlist (≥ 63 → BUY, < 45 → SELL, else HOLD). The live trader gates on the macro-aware threshold, not this static mapping.

---

## HMM Regime Detection

The system fits a 2-state full-covariance Gaussian HMM to normalised returns (rolling 63-day z-score). Rather than using the hard argmax label, it runs the posterior bull-state probability through a scalar **Kalman filter** (Q=0.01, R=0.1) to produce a smoothed signal.

| Smoothed bull probability | Regime label | Effect |
|---|---|---|
| > 0.65 | `bull` | — |
| 0.35 – 0.65 | `transition` | `score_deterioration` exits suppressed |
| < 0.35 | `bear` | — |

The **entry gate** is stricter than the label boundary: a ticker must have smoothed bull probability **≥ 0.70** to be eligible for a buy (`bull_prob_below_threshold` otherwise). If the HMM fit fails, the bull probability falls back to 0.5 and `hmm_fit_failed` is flagged downstream. Both `smoothed_bull_prob` and `raw_bull_prob` are returned by `/api/factors/{ticker}` and logged to `signal_log`.

---

## Macro Regime & Adaptive Thresholds

### Macro regime (per signal job)

Once per run the job checks **SPY against its 200-day moving average** (5-min cached, retried up to 3× on transient yfinance failures). This selects which buy threshold applies:

| SPY vs 200d MA | Regime | Buy threshold (default) |
|---|---|---|
| Above | bull | 63 |
| Below | bear | 80 |

If SPY data is unavailable after 3 attempts, the regime **fails open to bull** (the market sits above its 200d MA most of the time; failing closed would silently block nearly every trade). VIX is fetched alongside and **fails closed** (treated as elevated → blocks new buys). The current regime and provenance are exposed at `/api/macro-regime`.

### Adaptive thresholds (weekly)

Buy thresholds self-tune each Sunday at 18:00 ET using an **exponentially weighted average** (α = 0.15) on the last 50 closed trades per regime.

| Win rate | Target | Effect |
|---|---|---|
| 60% | Minimum (bull 63 / bear 75) | Loosen — signal edge is strong |
| 40% | Maximum (bull 80 / bear 85) | Tighten — signal edge is weak |

The mapping is linear between 40–60% win rate. Bull and bear thresholds adapt independently using only trades from matching-regime entries, and require ≥ 5 regime-matched trades to update; otherwise the current value is held. Each run is logged to `diagnostic_snapshots` (`threshold_audit`).

---

## Position Sizing

Positions are sized using **fractional Kelly** when trade history is available, falling back to volatility-targeting otherwise.

| Condition | Method | `sizing_method` |
|---|---|---|
| ≥ 10 closed trades for this ticker | Half-Kelly from ticker win rate and odds ratio | `kelly` |
| < 10 ticker trades but ≥ 20 portfolio-wide | Half-Kelly using portfolio-wide p and b as prior | `kelly_portfolio_prior` |
| Fewer than 20 portfolio trades | 1% daily vol target | `vol_target_fallback` |

**Kelly formula**: `f* = (p·b − q) / b`, where `p` = win rate, `q = 1−p`, `b` = avg win % / avg loss %. Half-Kelly (`f*/2`) is used to reduce variance.

**Safeguards**:
- Kelly capped at 3× the vol-target equivalent (logs a warning if triggered)
- Per-position hard cap: 10% of equity
- Per-position floor: 0.5% of equity
- **Correlation penalty**: a candidate's size is shrunk when it co-moves with an already-open position
- **Portfolio-wide normalization**: aggregate exposure across all BUYs in a run is capped at 100% of equity (`PORTFOLIO_KELLY_CAP`); positions pushed below the floor are dropped and survivors re-normalised

A **conviction multiplier** (score 75→1×, 95→1.5×) and **performance multiplier** (win rate > 60% → 1.2×, < 40% → 0.7×) apply on top in all sizing methods. `kelly_fraction` and `sizing_method` are written to `signal_log` on every BUY order.

---

## Sentiment Rate Limiting

Alpha Vantage's free tier allows 5 requests/minute. The signal job **pre-fetches sentiment sequentially** with a ~13-second gap per ticker before launching the parallel factor computation, so the parallel workers get cache hits rather than hammering the API simultaneously (15-min cache TTL).

If > 50% of watchlist tickers return null sentiment in a single run, a `sentiment_degraded` warning is written to `diagnostic_snapshots` with the null rate and timestamp.

---

## API Reference

### Signals and analysis

| Endpoint | Description |
|---|---|
| `GET /api/quote/{ticker}` | Current price and 1-day change |
| `GET /api/price-history/{ticker}` | OHLCV price history series |
| `GET /api/signal/{ticker}` | Price quote + Gaussian-HMM bull/bear regime label |
| `GET /api/backtest/{ticker}` | Walk-forward backtest (composite-threshold entry, ATR stop, 21-day hold, macro filter) |
| `GET /api/company/{ticker}` | Cached company name / sector info |

### Factor engine

| Endpoint | Description |
|---|---|
| `GET /api/factors/{ticker}` | Five-factor composite score with `smoothed_bull_prob`, `raw_bull_prob`, regime label |
| `GET /api/factors/cluster?tickers=A,B` | Batch factor summary for multiple tickers |
| `GET /api/factor-weights` | Active default factor weights |
| `GET /api/factor-correlations` | Cross-sectional Pearson correlation across watchlist |
| `GET /api/macro-regime` | SPY/200d-MA regime, active threshold provenance |

### Alternative data

| Endpoint | Description |
|---|---|
| `GET /api/sentiment/{ticker}` | Alpha Vantage news sentiment score, direction, article count |
| `GET /api/insider/{ticker}` | SEC EDGAR Form 4 insider activity (30d, open-market only) |
| `GET /api/shortinterest/{ticker}` | Finviz short float %, ratio, squeeze flag |

### Portfolio

| Endpoint | Description |
|---|---|
| `POST /api/portfolio/sizing` | Kelly + vol-targeted allocation with correlation penalties |
| `POST /api/portfolio/backtest` | Multi-ticker walk-forward backtest + efficient frontier |
| `GET /api/portfolio/history` | Realized equity curve from closed trades |
| `GET /api/portfolio/live-equity` | Current live equity snapshot |
| `GET /api/portfolio/positions/entry-signals` | Entry signal that opened each open position |
| `POST /api/portfolio/positions/close` | Manually close a position |
| `GET /api/portfolio/edge-stats` | Realized edge / win-rate statistics |

### Watchlist

| Endpoint | Description |
|---|---|
| `GET /api/watchlist` | All tickers in the persistent watchlist |
| `GET /api/watchlist/snapshot` | Cached factor snapshots for instant homepage render |
| `POST /api/watchlist/{ticker}` | Add a ticker |
| `POST /api/watchlist/{ticker}/refresh` | Force-recompute a ticker's snapshot |
| `DELETE /api/watchlist/{ticker}` | Remove a ticker and cancel open Alpaca orders |

### Paper trading and automation

| Endpoint | Description |
|---|---|
| `GET /api/paper/account` | Alpaca account equity, cash, buying power |
| `GET /api/paper/positions` | Open positions with P&L, trailing ATR stop, days held |
| `GET /api/paper/sector-exposure` | Open-position exposure grouped by sector |
| `GET /api/paper/equity-history` | Alpaca equity history series |
| `GET /api/paper/history` | Closed trade history with return %, exit reason |
| `GET /api/signals/log?limit=50` | Recent signal log entries |
| `POST /api/paper/run-now` | Manually trigger the signal job in a background thread |
| `GET /api/analytics` | Score calibration, exit breakdown, ticker performance, adaptive thresholds, system health |
| `GET /api/briefing` | Consolidated daily briefing |
| `GET /api/decision-trail/{ticker}` | Per-ticker gate-by-gate decision trail |

### Diagnostics and debug

| Endpoint | Description |
|---|---|
| `GET /api/gate-stats` | Gate rejection counts and rates over the last 90 days |
| `GET /api/weight-overrides` | Per-ticker factor weight override drift log |
| `GET /api/debug` | Consolidated view: gate stats, factor correlations, active config |
| `GET /api/debug/sentiment` | Calls `_get_sentiment_score()` for every watchlist ticker; returns scores, null count, null rate |
| `GET /api/debug/kelly` | Kelly parameters (p, b, f\*, half-f\*) for every watchlist ticker and the portfolio-wide prior |
| `GET /health` | Health check |
