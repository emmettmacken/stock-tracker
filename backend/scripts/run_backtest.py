#!/usr/bin/env python3
"""Local CLI for the portfolio backtest — bypasses the API/auth and prints a clean,
human-readable summary instead of dumping the full equity_curve / efficient_frontier
arrays to the terminal.

It calls ``main.portfolio_backtest`` in-process, so it reads BACKTEST_CONFIG exactly
like the HTTP endpoint (any override in ``config/backtest.py`` is picked up automatically)
and needs no running server, login cookie, or 15-minute token dance.

The full JSON response is unchanged — this script is presentation only. Use ``--json`` to
print the raw response, or ``--dump FILE`` to write the full arrays (equity_curve,
efficient_frontier, trade_log) to a file for later inspection.

Examples
--------
    # Pinned 28-ticker baseline, as-of 2026-06-30, clean summary:
    python scripts/run_backtest.py --as-of 2026-06-30

    # Custom universe + per-trade hold/excursion analysis:
    python scripts/run_backtest.py --tickers AAPL MSFT NVDA --trades

    # Raw JSON (for piping into jq / scripts):
    python scripts/run_backtest.py --as-of 2026-06-30 --json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import warnings

warnings.filterwarnings("ignore")

# Import the FastAPI app module from the backend root (this file lives in backend/scripts/).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# The pinned baseline universe (the 28 names in the local watchlist / config/base.py note).
BASELINE_28 = [
    "AAPL", "ABBV", "AMZN", "AVGO", "BLK", "CAT", "COST", "DE", "GE", "GOOG",
    "GS", "HD", "HON", "JNJ", "JPM", "LIN", "LLY", "MA", "META", "MRK",
    "MSFT", "NEE", "NVDA", "PLD", "TMO", "TSM", "V", "XOM",
]


def _fmt_pct(v):
    return "  n/a" if v is None else f"{v:+.2f}%"


def print_summary(r: dict, show_trades: bool) -> None:
    """Render the backtest response as a compact terminal report — no raw arrays."""
    W = 64
    line = "─" * W

    print(line)
    print("PORTFOLIO BACKTEST SUMMARY")
    print(line)
    tickers = r.get("tickers", [])
    print(f"Tickers ({len(tickers)}): {' '.join(tickers)}")
    print(f"Window : {r.get('window_start')} → {r.get('window_end')}"
          f"   (as_of={r.get('as_of_date') or 'live'})")
    print(f"Capital: ${r.get('capital'):,.2f}")
    print(line)

    # Headline performance
    tr, spy = r.get("total_return_pct"), r.get("spy_return_pct")
    edge = None if (tr is None or spy is None) else tr - spy
    print("PERFORMANCE")
    print(f"  Total return   : {_fmt_pct(tr)}")
    print(f"  SPY return     : {_fmt_pct(spy)}")
    print(f"  vs SPY         : {_fmt_pct(edge)}")
    print(f"  Sharpe ratio   : {r.get('sharpe_ratio')}")
    print(f"  Max drawdown   : {_fmt_pct(r.get('max_drawdown_pct'))}")
    print(line)

    # Exit / turnover accounting
    gc = r.get("gate_comparison", {})
    print("EXITS / TURNOVER")
    print(f"  Total rebalances : {gc.get('total_rebalances')}")
    print(f"  Profit-takes     : {r.get('profit_takes')}")
    print(f"  Stop-loss exits  : {r.get('stop_loss_exits')}")
    print(f"  Mid-window exits : {gc.get('mid_window_exits')}")
    print(line)

    # Per-ticker contribution, best → worst
    contrib = r.get("per_ticker_contrib", {})
    if contrib:
        print("PER-TICKER CONTRIBUTION (best → worst, % of capital)")
        for t, v in sorted(contrib.items(), key=lambda kv: kv[1], reverse=True):
            bar_len = int(min(abs(v), 4.0) / 4.0 * 20)
            bar = ("█" * bar_len).ljust(20)
            print(f"  {t:<6} {v:+7.3f}%  {bar}")
        print(line)

    # Rebalance events — one line each, only the BUY/HOLD signals that CHANGED vs prior event
    events = r.get("rebalance_events", [])
    if events:
        print("REBALANCE EVENTS (Δ = signals changed from prior event)")
        prev = {}
        for ev in events:
            sigs = ev.get("signals", {})
            changes = [f"{t}:{s}" for t, s in sorted(sigs.items()) if prev.get(t) != s]
            n_buys = sum(1 for s in sigs.values() if s == "BUY")
            delta = ", ".join(changes) if changes else "(no signal changes)"
            print(f"  {ev.get('date')}  buys={n_buys:<2}  Δ {delta}")
            prev = sigs
        print(line)

    if show_trades and "trade_log" in r:
        print_trade_analysis(r["trade_log"])


def print_trade_analysis(trades: list) -> None:
    """Hold-time and excursion breakdown by exit type (needs include_trade_log)."""
    import statistics as st
    W = 64
    line = "─" * W
    print("TRADE LOG ANALYSIS")
    if not trades:
        print("  (no trades)")
        print(line)
        return
    by_type: dict[str, list] = {}
    for t in trades:
        by_type.setdefault(t["exit_type"], []).append(t)
    print(f"  {'exit_type':<16}{'n':>4}{'avg_hold':>10}{'avg_ret%':>10}{'avg_mfe%':>10}")
    for typ in ("profit_take", "stop_loss", "mid_window", "window_boundary"):
        rows = by_type.get(typ)
        if not rows:
            continue
        avg_hold = st.mean(x["hold_days"] for x in rows)
        avg_ret = st.mean(x["return_pct"] for x in rows)
        avg_mfe = st.mean(x["mfe_pct"] for x in rows)
        print(f"  {typ:<16}{len(rows):>4}{avg_hold:>10.1f}{avg_ret:>+10.2f}{avg_mfe:>+10.2f}")
    print(line)


def main_cli() -> None:
    p = argparse.ArgumentParser(description="Run the portfolio backtest locally (no API/auth).")
    p.add_argument("--tickers", nargs="+", default=None,
                   help="Ticker universe (default: pinned 28-ticker baseline).")
    p.add_argument("--capital", type=float, default=10000.0)
    p.add_argument("--as-of", dest="as_of", default=None,
                   help="YYYY-MM-DD pin (default: today).")
    p.add_argument("--json", action="store_true", help="Print the raw full JSON response.")
    p.add_argument("--dump", metavar="FILE", default=None,
                   help="Write the full response (incl. equity_curve/efficient_frontier) to FILE.")
    p.add_argument("--trades", action="store_true",
                   help="Capture + show the per-trade hold/excursion analysis.")
    args = p.parse_args()

    import main  # imported after sys.path fix; heavy (numpy, yfinance) so keep it lazy
    from diagnostics import TradeTap

    tickers = args.tickers or BASELINE_28
    req = main.PortfolioBacktestRequest(
        tickers=tickers,
        capital=args.capital,
        as_of_date=args.as_of,
    )

    # Per-trade log is reconstructed by the diagnostics tap (main.py itself stays clean).
    want_trades = args.trades or bool(args.dump)
    tap = TradeTap() if want_trades else None
    main.TRADE_TAP = tap
    try:
        r = main.portfolio_backtest(req)
    finally:
        main.TRADE_TAP = None
    if tap is not None:
        r["trade_log"] = tap.build_trade_log()

    if args.dump:
        with open(args.dump, "w") as f:
            json.dump(r, f, indent=2)
        print(f"[dumped full response → {args.dump}]", file=sys.stderr)

    if args.json:
        print(json.dumps(r, indent=2))
    else:
        print_summary(r, show_trades=args.trades)


if __name__ == "__main__":
    main_cli()
