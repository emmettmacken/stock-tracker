"""Diagnostics layer for the portfolio backtest — reconstructs a per-trade log from the
raw entry/exit primitives emitted by ``main.TRADE_TAP``.

ALL derived-metric computation (hold length, max-favorable-excursion, the post-exit forward
peak, and the empirical ``mult_to_hold`` stop-width counterfactual) lives here, so main.py
carries only inert emit hooks and stays in its verified bit-identical state.

Usage
-----
    import main
    from scripts.diagnostics import TradeTap

    tap = TradeTap()
    main.TRADE_TAP = tap
    try:
        r = main.portfolio_backtest(req)   # metrics identical whether or not the tap is set
    finally:
        main.TRADE_TAP = None
    trades = tap.build_trade_log()
"""
from __future__ import annotations

import numpy as np

# Same ATR fallback the backtest uses at entry (only hit when entry ATR is NaN/≤0), read from
# the same config object main.py resolves against so the reconstruction stays consistent.
try:
    from config import BACKTEST_CONFIG
    _ATR_FALLBACK_PCT = BACKTEST_CONFIG.factors.atr_fallback_pct
except Exception:  # pragma: no cover — config import is always available in-process
    _ATR_FALLBACK_PCT = 0.02


class TradeTap:
    """Collects raw entry/exit events during a backtest run, then computes per-trade metrics.

    Mirrors main.py's entry lifecycle exactly: an entry is recorded once per holding; a
    ``profit_take`` is a TRIM (entry basis persists, so the remaining half can later log a
    second exit); every other exit type is a full close that clears the entry.
    """

    def __init__(self, fwd_horizon: int = 63, hold_horizon: int = 126):
        self._fwd = fwd_horizon      # post-exit window for "did it later hit +15%?" (~3mo)
        self._hold = hold_horizon    # from-entry window for the mult_to_hold probe (~6mo)
        self._features = None
        self._atr = None
        self._dates = None
        self._pt = None
        self._entries: dict[str, tuple[int, float]] = {}   # ticker -> (entry_idx, entry_px)
        self._raw: list[tuple] = []  # (ticker, entry_idx, entry_px, exit_idx, exit_px, type)

    # ── tap interface (called by main.py; no computation here) ────────────────
    def context(self, features, atr_series, dates, profit_take_pct) -> None:
        self._features, self._atr, self._dates, self._pt = (
            features, atr_series, dates, profit_take_pct,
        )

    def entry(self, ticker: str, idx: int, px: float) -> None:
        self._entries[ticker] = (idx, float(px))

    def exit(self, ticker: str, idx: int, px: float, exit_type: str) -> None:
        e = self._entries.get(ticker)
        if e is None:
            return
        entry_idx, entry_px = e
        self._raw.append((ticker, entry_idx, entry_px, idx, float(px), exit_type))
        if exit_type != "profit_take":   # full close clears the basis; trim keeps it
            self._entries.pop(ticker, None)

    # ── derived-metric computation ────────────────────────────────────────────
    def build_trade_log(self) -> list[dict]:
        """One record per collected exit, with hold/excursion/counterfactual metrics."""
        out: list[dict] = []
        for ticker, entry_idx, entry_px, exit_idx, exit_px, exit_type in self._raw:
            if entry_px is None or entry_px <= 0:
                continue
            c_full = self._features[ticker][0]

            seg = c_full[entry_idx: exit_idx + 1]
            mfe = float((np.nanmax(seg) - entry_px) / entry_px) if len(seg) else 0.0
            fwd = c_full[exit_idx: min(exit_idx + self._fwd + 1, len(c_full))]
            fwd_peak = float((np.nanmax(fwd) - entry_px) / entry_px) if len(fwd) else 0.0

            _ae = self._atr[ticker][entry_idx]
            atr0 = float(_ae) if (not np.isnan(_ae) and _ae > 0) else entry_px * _ATR_FALLBACK_PCT
            atr_pct = atr0 / entry_px if entry_px > 0 else 0.0

            # Walk forward from entry; find the first +15% touch and the deepest give-back from
            # the running peak up to that touch (in entry-ATR units) = min trailing-stop mult
            # that would have kept the position alive to profit-take.
            reached_pt = False
            peak = entry_px
            worst_giveback_atr = 0.0
            path = c_full[entry_idx: min(entry_idx + self._hold + 1, len(c_full))]
            for px in path:
                if np.isnan(px):
                    continue
                if px > peak:
                    peak = px
                gb_atr = (peak - px) / atr0 if atr0 > 0 else 0.0
                if gb_atr > worst_giveback_atr:
                    worst_giveback_atr = gb_atr
                if (px - entry_px) / entry_px >= self._pt:
                    reached_pt = True
                    break

            out.append({
                "ticker": ticker,
                "entry_date": str(self._dates[entry_idx])[:10],
                "exit_date": str(self._dates[exit_idx])[:10],
                "exit_type": exit_type,
                "hold_days": int(exit_idx - entry_idx),
                "entry_px": round(entry_px, 4),
                "exit_px": round(float(exit_px), 4),
                "return_pct": round((float(exit_px) - entry_px) / entry_px * 100, 3),
                "mfe_pct": round(mfe * 100, 3),
                "fwd_peak_pct": round(fwd_peak * 100, 3),
                "atr_pct_entry": round(atr_pct * 100, 3),
                "reached_pt_fwd": reached_pt,
                "mult_to_hold": round(worst_giveback_atr, 2),
            })
        return out
