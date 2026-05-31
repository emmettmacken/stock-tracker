# Stock Signal Tracker

2-D Markov chain signals, Kalman-filtered HMM regime detection, six-factor composite scoring, fractional Kelly position sizing, alternative data (news sentiment, EDGAR insider filings), walk-forward backtesting, and automated paper trading via Alpaca.

## Stack

- **Backend**: Python 3.12 · FastAPI · yfinance · hmmlearn · APScheduler · SQLite · Alpaca SDK (port 8000)
- **Frontend**: Next.js 14 (App Router) · TypeScript · Tailwind CSS v3 · Recharts (port 3000)

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

Open `http://localhost:3000`.

---

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `ALPACA_API_KEY` | Yes | Alpaca paper trading key (`PKxxxxxxxx`) |
| `ALPACA_SECRET_KEY` | Yes | Alpaca secret |
| `ALPACA_BASE_URL` | Yes | `https://paper-api.alpaca.markets` |
| `ALPHA_VANTAGE_KEY` | Recommended | News sentiment scores (free tier: 5 req/min) |
| `DB_PATH` | Optional | SQLite path (default: `backend/stock_tracker.db`) |
| `CORS_ORIGINS` | Optional | Comma-separated frontend origins for production |

---

## Railway Deployment (persistent backend)

Railway keeps the backend alive 24/7 so APScheduler jobs fire on schedule even when your laptop is off.

### Steps

1. **Push your code** to a GitHub repo (the `backend/` folder is the service root).

2. **Create a Railway project** → New Service → GitHub Repo → select your repo.

3. **Set the root directory** to `backend/` (Settings → Source → Root Directory).

4. **Add environment variables** (Settings → Variables) — see table above.

5. **Add a persistent Volume** so the SQLite database survives redeploys:
   - Railway dashboard → your service → Volumes → Add Volume
   - Mount path: `/data`
   - Set `DB_PATH=/data/stock_tracker.db` in env vars.

6. **Deploy** — Railway detects `Procfile` and runs:
   ```
   uvicorn main:app --host 0.0.0.0 --port $PORT --workers 1
   ```
   `--workers 1` is required — multiple workers each start their own APScheduler, causing duplicate job firings.

7. **Update your frontend** `lib/api.ts` to point `BASE` at the Railway URL.

---

## Alpaca Paper Trading Setup

1. Sign up at **https://alpaca.markets** → Paper Trading → API Keys → generate a key pair.
2. Add keys to `backend/.env` and restart the backend.

The system runs three scheduled jobs (all times ET):

| Time | Days | Job | What it does |
|---|---|---|---|
| 09:35 | Mon–Fri | Stop-loss check | Closes positions where price fell below trailing ATR stop or hold > 21 trading days; closes all positions if SPY fell >3% over 5 days |
| 15:30 | Mon–Fri | Signal job | Sequentially pre-fetches sentiment, then computes all factors in parallel, then evaluates each ticker through the gate stack and places/closes Alpaca paper orders |
| 18:00 | Sunday | Adaptive thresholds | Recomputes buy thresholds using EWA on last 50 trades per regime |

To test without waiting for market hours: **"Run signals now"** on the Automation page, or `POST /api/paper/run-now`.

---

## Signal Pipeline

Each run of the signal job evaluates every watchlist ticker through a sequential gate stack. A ticker must pass every gate to receive a buy order.

### Gate stack (in order)

| Gate | Skip reason | Condition |
|---|---|---|
| Earnings proximity | `earnings_within_2d` | Earnings announcement within 2 calendar days |
| Data availability | `data_unavailable` | yfinance failed to return sufficient history |
| Raw HMM signal | `hmm_not_buy` | Markov chain Wilson CI signal is not BUY |
| Kalman regime | `hmm_regime_uncertain` | Kalman-smoothed bull probability ≤ 0.65 |
| Composite score | `score_below_threshold:X<Y` | Composite score below adaptive buy threshold |
| Sentiment | `sentiment_too_low:X` | Sentiment score < 35 (bearish news flow) |
| VIX | `vix_too_high:X` | VIX > 30 |
| Already held | `already_in_position` | Ticker already in open positions |
| Overextension | `overextended` | Price > MA20 × 1.25 and momentum not top-quartile |
| Momentum | `momentum_disagreement` | 3m + 12m return ≤ 0, or either < −10% |
| Re-entry cooldown | `reentry_cooldown` | Closed within last 5 trading days |
| Sector cap | `sector_concentration` | Sector already has ≥ 3 open positions |

### Exit conditions

| Exit reason | Trigger |
|---|---|
| `score_deterioration` | Composite score < 40 (skipped in transition regime) |
| `sell_signal` | HMM signal = SELL and composite < 45 |
| `stop_loss` | Price falls below trailing ATR stop (entry − 1.5 × ATR, raised as price rises) |
| `max_hold_exit` | Position held > 21 trading days |
| `macro_drawdown_protection` | SPY fell > 3% over last 5 trading days — all positions closed |

---

## Factor Engine

The composite score is a weighted average of six factors (0–100 each). Null factors (data unavailable) are dropped and weights renormalise automatically.

| Factor | Default weight | Source | What it measures |
|---|---|---|---|
| `hmm` | 20% | hmmlearn GaussianHMM | Markov chain BUY signal strength × CI confidence |
| `momentum` | 25% | yfinance | 3m + 12m return z-scored against rolling history (21-day skip) |
| `vol_trend` | 20% | yfinance | Price alignment with MA20/MA50/MA200, vol-adjusted |
| `earnings` | 18% | yfinance earnings history | EPS surprise direction and magnitude (last 2 quarters) |
| `insider` | 5% | SEC EDGAR Form 4 (structured submissions API) | Net open-market purchases vs sales, role-weighted, 30-day lookback |
| `sentiment` | 12% | Alpha Vantage NEWS_SENTIMENT | Average article sentiment score mapped to 0–100 |

Factor weights can be overridden per ticker via `factor_weight_overrides.json`.

---

## HMM Regime Detection

The system fits a 2-state Gaussian HMM to normalised returns (rolling 63-day z-score). Rather than using the hard argmax label, it runs the posterior bull-state probability through a scalar **Kalman filter** (Q=0.01, R=0.1) to produce a smoothed signal.

| Smoothed bull probability | Regime | Entry gate |
|---|---|---|
| > 0.65 | `bull` | Allowed (uses bull threshold) |
| 0.35 – 0.65 | `transition` | Blocked (`hmm_regime_uncertain`); score-deterioration exits suppressed |
| < 0.35 | `bear` | Blocked (`hmm_regime_uncertain`) |

Both `smoothed_bull_prob` and `raw_bull_prob` are returned by `/api/factors/{ticker}` and logged to `signal_log` for comparison.

---

## Position Sizing

Positions are sized using **fractional Kelly** when trade history is available, falling back to volatility-targeting otherwise.

| Condition | Method | `sizing_method` |
|---|---|---|
| ≥ 10 closed trades for this ticker | Half-Kelly from ticker win rate and odds ratio | `kelly` |
| < 10 ticker trades but ≥ 20 portfolio-wide | Half-Kelly using portfolio-wide p and b as prior | `kelly_portfolio_prior` |
| Fewer than 20 portfolio trades | 1% daily vol target (original formula) | `vol_target_fallback` |

**Kelly formula**: `f* = (p·b − q) / b`, where `p` = win rate, `q = 1−p`, `b` = avg win % / avg loss %. Half-Kelly (`f*/2`) is used to reduce variance.

**Safeguards**:
- Kelly capped at 3× the vol-target equivalent (logs a warning if triggered)
- Hard cap: 10% of equity
- Floor: 0.5% of equity

A **conviction multiplier** (score 75→1×, 95→1.5×) and **performance multiplier** (win rate > 60% → 1.2×, < 40% → 0.7×) apply on top in all sizing methods.

`kelly_fraction` and `sizing_method` are written to `signal_log` on every BUY order.

---

## Adaptive Threshold System

Buy thresholds self-tune weekly using an **exponentially weighted average** (α = 0.15) on the last 50 closed trades per regime.

| Win rate | Target threshold | Effect |
|---|---|---|
| 60% | Minimum (65 bull / 75 bear) | Loosen — signal edge is strong |
| 40% | Maximum (80 bull / 85 bear) | Tighten — signal edge is weak |

The mapping is linear between 40–60% win rate. Bull and bear thresholds adapt independently using only trades from matching-regime entries. Requires ≥ 5 regime-matched trades to update; otherwise the current value is held. The weekly job runs every Sunday at 18:00 ET.

---

## Sentiment Rate Limiting

Alpha Vantage's free tier allows 5 requests/minute. The signal job **pre-fetches sentiment sequentially** with a 13-second gap per ticker before launching the parallel factor computation. This ensures all parallel workers get cache hits for sentiment rather than hammering the API simultaneously.

If > 50% of watchlist tickers return null sentiment in a single job run, a `sentiment_degraded` warning is written to `diagnostic_snapshots` with the null rate and timestamp.

---

## API Reference

### Signals and analysis

| Endpoint | Description |
|---|---|
| `GET /api/quote/{ticker}` | Current price and 1-day change |
| `GET /api/signal/{ticker}` | Full Markov v2 signal: regime, CI, transition matrix, heatmap |
| `GET /api/backtest/{ticker}` | Walk-forward backtest with gate comparison and transaction costs |

### Factor engine

| Endpoint | Description |
|---|---|
| `GET /api/factors/{ticker}` | Six-factor composite score with `smoothed_bull_prob`, `raw_bull_prob`, HMM signal |
| `GET /api/factors/cluster?tickers=A,B` | Batch factor summary for multiple tickers |
| `GET /api/factor-weights` | Active default factor weights |
| `GET /api/factor-correlations` | Cross-sectional Pearson correlation across watchlist |

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

### Watchlist

| Endpoint | Description |
|---|---|
| `GET /api/watchlist` | All tickers in the persistent watchlist |
| `POST /api/watchlist/{ticker}` | Add a ticker |
| `DELETE /api/watchlist/{ticker}` | Remove a ticker and cancel open Alpaca orders |

### Paper trading and automation

| Endpoint | Description |
|---|---|
| `GET /api/paper/account` | Alpaca account equity, cash, buying power |
| `GET /api/paper/positions` | Open positions with P&L, trailing ATR stop, days held |
| `GET /api/paper/history` | Closed trade history with return %, exit reason |
| `GET /api/signals/log?limit=50` | Recent signal log entries |
| `POST /api/paper/run-now` | Manually trigger signal job in background thread |
| `GET /api/analytics` | Score calibration, exit breakdown, ticker performance, adaptive thresholds, system health |

### Diagnostics and debug

| Endpoint | Description |
|---|---|
| `GET /api/gate-stats` | Gate rejection counts and rates over the last 90 days |
| `GET /api/weight-overrides` | Per-ticker factor weight override drift log |
| `GET /api/debug` | Consolidated view: gate stats, factor correlations, active config |
| `GET /api/debug/sentiment` | Calls `_get_sentiment_score()` for every watchlist ticker sequentially; returns scores, null count, null rate |
| `GET /api/debug/kelly` | Kelly parameters (p, b, f\*, half-f\*) for every watchlist ticker and portfolio-wide prior |
| `GET /health` | Health check |
