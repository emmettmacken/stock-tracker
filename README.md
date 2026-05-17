# Stock Signal Tracker

Markov-chain / HMM buy-sell-hold signals, multi-factor scoring, alternative data, portfolio sizing, walk-forward backtesting, and automated paper trading via Alpaca.

## Stack

- **Backend**: Python 3.10+ · FastAPI · yfinance · hmmlearn · APScheduler · SQLite · Alpaca SDK (port 8000)
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

## Railway Deployment (persistent backend)

Railway keeps the backend alive 24/7 so APScheduler jobs fire at 09:35 and 15:45 ET even when your laptop is off.

### Steps

1. **Push your code** to a GitHub repo (the `backend/` folder is the service root).

2. **Create a Railway project** → New Service → GitHub Repo → select your repo.

3. **Set the root directory** to `backend/` in the Railway service settings (Settings → Source → Root Directory).

4. **Add environment variables** in Railway (Settings → Variables):

   | Variable | Value |
   |---|---|
   | `FINNHUB_API_KEY` | Your Finnhub key |
   | `ALPACA_API_KEY` | `PKxxxxxxxxxxxxxxxx` |
   | `ALPACA_SECRET_KEY` | `xxxxxxxx…` |
   | `ALPACA_BASE_URL` | `https://paper-api.alpaca.markets` |
   | `DB_PATH` | `/data/stock_tracker.db` |
   | `CORS_ORIGINS` | Your frontend URL (e.g. `https://your-app.vercel.app`) |

5. **Add a persistent Volume** so the SQLite database survives redeploys:
   - Railway dashboard → your service → Volumes → Add Volume
   - Mount path: `/data`
   - Railway automatically injects the volume at that path.

6. **Deploy** — Railway detects `Procfile` and runs:
   ```
   uvicorn main:app --host 0.0.0.0 --port $PORT --workers 1
   ```
   The `--workers 1` is required: multiple workers each start their own APScheduler, causing jobs to fire multiple times.

7. **Update your frontend** `lib/api.ts` to point `BASE` at the Railway URL instead of `http://localhost:8000`.

### Notes

- Railway provides `PORT` automatically — the Procfile reads it via `$PORT`.
- The free Hobby plan ($5/mo) gives you persistent uptime. The free tier sleeps after inactivity, which would break the scheduler — avoid it for this use case.
- To check scheduler health, hit `GET /health` on the Railway URL.

---

## Alpaca Paper Trading Setup

1. Sign up for a free account at **https://alpaca.markets**
2. Go to **Paper Trading** → API Keys → generate a key pair
3. Add to `backend/.env`:

```env
ALPACA_API_KEY=PKxxxxxxxxxxxxxxxx
ALPACA_SECRET_KEY=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
ALPACA_BASE_URL=https://paper-api.alpaca.markets
```

4. Restart the backend — the scheduler starts automatically.

The system runs two scheduled jobs (Monday–Friday only):

| Time (ET) | Job | What it does |
|---|---|---|
| 09:35 | Stop-loss check | Closes positions where price has fallen > 1.5× ATR from entry, or held > 21 trading days |
| 15:45 | Signal job | For each watchlist ticker: checks earnings calendar, computes composite factor score, applies macro regime gate, places/closes Alpaca paper orders |

To test without waiting for market hours, use the **"Run signals now"** button on the Automation page, or `POST /api/paper/run-now`.

---

## Adaptive Threshold System

Buy thresholds self-tune weekly based on recent signal performance:

| Condition (last 20 trades) | Effect |
|---|---|
| Win rate > 60% | Bull threshold lowered by 5 pts, bear threshold lowered by 5 pts |
| Win rate < 40% | Bull threshold raised by 5 pts, bear threshold raised by 5 pts |
| Otherwise | No change |

Bounds: bull threshold 65–80, bear threshold 75–85. Defaults are 70 (bull) / 80 (bear). Values persist in `system_config` and are applied by the signal job at runtime. The weekly adjustment runs every Sunday at 18:00 ET.

---

## Macro Regime Gate

| Condition | Effect |
|---|---|
| SPY below 200-day MA | Buy threshold raised to adaptive bear threshold (default 85) |
| VIX > 30 | All new buys skipped regardless of score |
| Earnings within 2 days | Ticker skipped entirely for that session |
| SPY fell > 3% over last 5 trading days | All open positions closed immediately (macro_drawdown_protection) |

---

## Signal Log — How to Read It

Each row in the signal log records one decision per ticker per scheduled run:

| `action` | Meaning |
|---|---|
| `ordered` | Market buy order placed with Alpaca |
| `closed` | Position closed (sell signal, stop-loss, or time exit) |
| `skipped` | No action taken — see `skip_reason` |

Common `skip_reason` values:

| Reason | What triggered it |
|---|---|
| `hold_or_below_threshold` | Signal was HOLD, or BUY but composite score too low |
| `already_in_position` | Ticker already held — no pyramid buying |
| `earnings_within_2d` | Earnings announcement imminent |
| `vix_too_high:XX.X` | VIX exceeded 30 |
| `data_unavailable` | yfinance failed to return data |
| `momentum_disagreement` | 3-month or 12-month return is negative (avoid short-term bounces in downtrends) |
| `reentry_cooldown` | Ticker closed for a non-signal reason within the last 5 trading days |
| `sector_concentration` | Sector already has 2 open positions |
| `low_volume` | Volume below 1.2× 20-day average |
| `overextended` | Price more than 15% above 20-day MA |
| `friday_no_entry` | No new positions opened on Fridays |

---

## API Reference

### V2 (Markov signal)

| Endpoint | Description |
|---|---|
| `GET /api/quote/{ticker}` | Current price and 1-day change |
| `GET /api/signal/{ticker}` | Full Markov v2 signal with regime, CI, heatmap |
| `GET /api/backtest/{ticker}` | Walk-forward backtest results over 2-year history |
| `GET /health` | Health check |

### V3 (Factor engine + alternative data + portfolio)

| Endpoint | Description |
|---|---|
| `GET /api/factors/{ticker}` | Composite 0–100 score from 5 weighted factors |
| `GET /api/sentiment/{ticker}` | Finnhub news sentiment (requires `FINNHUB_API_KEY`) |
| `GET /api/insider/{ticker}` | SEC EDGAR Form 4 insider activity (30d) |
| `GET /api/shortinterest/{ticker}` | Finviz short float %, ratio, squeeze flag |
| `POST /api/portfolio/sizing` | Kelly + vol-targeted allocation with correlation penalties |
| `POST /api/portfolio/backtest` | Multi-ticker walk-forward backtest + efficient frontier |

### V4 (Automation + paper trading)

| Endpoint | Description |
|---|---|
| `GET /api/watchlist` | All tickers in the persistent watchlist |
| `POST /api/watchlist/{ticker}` | Add a ticker |
| `DELETE /api/watchlist/{ticker}` | Remove a ticker + cancel open Alpaca orders |
| `GET /api/paper/account` | Alpaca account equity, cash, buying power |
| `GET /api/paper/positions` | Open positions with P&L, ATR stop, days held |
| `GET /api/paper/history` | Closed trade history with return %, exit reason |
| `GET /api/signals/log?limit=50` | Recent signal log entries |
| `POST /api/paper/run-now` | Manually trigger signal job (background thread) |
| `GET /api/analytics` | Score calibration, exit breakdown, ticker performance, adaptive thresholds, system health |

---

## Position Sizing

Positions are sized using **volatility-targeting**: each position is sized so it contributes approximately 1% daily volatility to the portfolio, then capped at 10% of account equity. The Kelly fraction (displayed in the Portfolio page) is computed as a secondary reference but is not used for live order sizing.

Stop-loss level = entry price − 1.5 × 21-day ATR at signal time.
