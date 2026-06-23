"""SQLite persistence layer for watchlist, signal log, and trade outcomes."""
from __future__ import annotations
import json
import os
import sqlite3
from datetime import datetime
from typing import Optional

DB_PATH = os.getenv(
    "DB_PATH",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "stock_tracker.db"),
)


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    with _conn() as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS watchlist (
                ticker   TEXT PRIMARY KEY,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS signal_log (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker          TEXT NOT NULL,
                timestamp       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                composite_score REAL,
                signal          TEXT,
                action          TEXT,
                skip_reason     TEXT,
                price_at_signal REAL,
                atr_at_signal   REAL
            );

            CREATE TABLE IF NOT EXISTS trade_outcomes (
                id                       INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker                   TEXT NOT NULL,
                entry_signal_id          INTEGER REFERENCES signal_log(id),
                entry_price              REAL,
                exit_price               REAL,
                exit_reason              TEXT,
                return_pct               REAL,
                holding_days             INTEGER,
                composite_score_at_entry REAL,
                exit_timestamp           TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS ticker_performance (
                ticker        TEXT PRIMARY KEY,
                total_trades  INTEGER DEFAULT 0,
                win_rate      REAL DEFAULT 0.5,
                avg_return    REAL DEFAULT 0.0,
                last_updated  TIMESTAMP,
                last_exit_at  TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS system_config (
                key        TEXT PRIMARY KEY,
                value      TEXT,
                updated_at TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS diagnostic_snapshots (
                key        TEXT PRIMARY KEY,
                data       TEXT NOT NULL,
                updated_at TIMESTAMP NOT NULL
            );

            -- Cached per-ticker display data, written by the signal job (and on-demand
            -- refresh) so the homepage can render without any live computation.
            CREATE TABLE IF NOT EXISTS latest_snapshot (
                ticker           TEXT PRIMARY KEY,
                composite_score  REAL,
                signal           TEXT,
                hmm_regime       TEXT,
                price            REAL,
                price_change_pct REAL,
                factors_json     TEXT,
                computed_at      TIMESTAMP NOT NULL
            );
        """)
    _run_migrations()


def _run_migrations() -> None:
    """Add new columns to existing tables; safe to run on every startup."""
    new_columns = [
        ("signal_log",     "ALTER TABLE signal_log ADD COLUMN current_stop REAL"),
        ("signal_log",     "ALTER TABLE signal_log ADD COLUMN stop_updated_at TIMESTAMP"),
        # Change 2: sentiment score tracked per signal for factor contribution analysis
        ("signal_log",     "ALTER TABLE signal_log ADD COLUMN sentiment_score REAL"),
        # Change 4: HMM regime at signal time, used by adaptive threshold job
        ("signal_log",     "ALTER TABLE signal_log ADD COLUMN hmm_regime TEXT"),
        # Change 4: regime at entry, lets threshold job separate bull/bear trades
        ("trade_outcomes", "ALTER TABLE trade_outcomes ADD COLUMN regime_at_entry TEXT"),
        # Kalman-smoothed HMM bull probability at signal time
        ("signal_log",     "ALTER TABLE signal_log ADD COLUMN smoothed_bull_prob REAL"),
        # Kelly position sizing audit fields
        ("signal_log",     "ALTER TABLE signal_log ADD COLUMN kelly_fraction REAL"),
        ("signal_log",     "ALTER TABLE signal_log ADD COLUMN sizing_method TEXT"),
        # Fix 2-D: flag when the HMM fit/predict failed (bull_prob=0.5 was a fallback, not neutral)
        ("signal_log",     "ALTER TABLE signal_log ADD COLUMN hmm_fit_failed INTEGER"),
        # Persist real position sizing on the ordered row, so future trades have a
        # trustworthy basis for dollar P&L. Nullable: historical rows stay NULL (we
        # never captured this and must not fake it).
        ("signal_log",     "ALTER TABLE signal_log ADD COLUMN entry_dollars REAL"),
        ("signal_log",     "ALTER TABLE signal_log ADD COLUMN equity_at_entry REAL"),
    ]
    with _conn() as conn:
        before_cols = {r[1] for r in conn.execute("PRAGMA table_info(signal_log)")}
        print(
            f"[migrations] migrating DB at {os.path.abspath(DB_PATH)} — "
            f"signal_log has {len(before_cols)} columns before migration"
        )
        for _, sql in new_columns:
            try:
                conn.execute(sql)
            except Exception:
                pass  # column already exists
        after_cols = {r[1] for r in conn.execute("PRAGMA table_info(signal_log)")}
        added = sorted(after_cols - before_cols)
        print(
            f"[migrations] signal_log has {len(after_cols)} columns after migration — "
            f"{'added ' + ', '.join(added) if added else 'no schema changes'}"
        )


# ── Watchlist ─────────────────────────────────────────────────────────────────

def get_watchlist() -> list[dict]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT ticker, added_at FROM watchlist ORDER BY added_at"
        ).fetchall()
    return [dict(r) for r in rows]


def add_ticker(ticker: str) -> None:
    with _conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO watchlist (ticker, added_at) VALUES (?, ?)",
            (ticker.upper(), datetime.utcnow().isoformat()),
        )


def remove_ticker(ticker: str) -> None:
    with _conn() as conn:
        conn.execute("DELETE FROM watchlist WHERE ticker = ?", (ticker.upper(),))
        conn.execute("DELETE FROM latest_snapshot WHERE ticker = ?", (ticker.upper(),))


# ── Display snapshots ──────────────────────────────────────────────────────────

def upsert_snapshot(
    ticker: str,
    composite_score: Optional[float],
    signal: Optional[str],
    hmm_regime: Optional[str],
    price: Optional[float],
    price_change_pct: Optional[float],
    factors: Optional[dict],
) -> None:
    """Write/replace the cached display snapshot for a ticker.

    `factors` is the full factor breakdown (the /api/factors payload) stored as JSON
    so the homepage and detail view can render without any live computation.
    """
    with _conn() as conn:
        conn.execute(
            """INSERT INTO latest_snapshot
                   (ticker, composite_score, signal, hmm_regime, price,
                    price_change_pct, factors_json, computed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(ticker) DO UPDATE SET
                   composite_score=excluded.composite_score,
                   signal=excluded.signal,
                   hmm_regime=excluded.hmm_regime,
                   price=excluded.price,
                   price_change_pct=excluded.price_change_pct,
                   factors_json=excluded.factors_json,
                   computed_at=excluded.computed_at""",
            (
                ticker.upper(),
                composite_score,
                signal,
                hmm_regime,
                price,
                price_change_pct,
                json.dumps(factors) if factors is not None else None,
                datetime.utcnow().isoformat(),
            ),
        )


def _row_to_snapshot(row: sqlite3.Row) -> dict:
    d = dict(row)
    raw = d.pop("factors_json", None)
    try:
        d["factors"] = json.loads(raw) if raw else None
    except (json.JSONDecodeError, TypeError):
        d["factors"] = None
    return d


def get_snapshot(ticker: str) -> Optional[dict]:
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM latest_snapshot WHERE ticker = ?", (ticker.upper(),)
        ).fetchone()
    return _row_to_snapshot(row) if row else None


def get_watchlist_snapshots() -> list[dict]:
    """Return one row per watchlist ticker, joined to its cached snapshot.

    Single fast read used by the homepage. Tickers with no snapshot yet come back
    with null fields (so the UI can show a "Calculating…" state).
    """
    with _conn() as conn:
        rows = conn.execute(
            """SELECT w.ticker            AS ticker,
                      w.added_at          AS added_at,
                      s.composite_score   AS composite_score,
                      s.signal            AS signal,
                      s.hmm_regime        AS hmm_regime,
                      s.price             AS price,
                      s.price_change_pct  AS price_change_pct,
                      s.factors_json      AS factors_json,
                      s.computed_at       AS computed_at
               FROM watchlist w
               LEFT JOIN latest_snapshot s ON s.ticker = w.ticker
               ORDER BY w.added_at"""
        ).fetchall()
    return [_row_to_snapshot(r) for r in rows]


# ── Signal log ────────────────────────────────────────────────────────────────

def log_signal(
    ticker: str,
    composite_score: Optional[float],
    signal: Optional[str],
    action: str,
    skip_reason: Optional[str],
    price_at_signal: Optional[float],
    atr_at_signal: Optional[float],
    hmm_regime: Optional[str] = None,
    sentiment_score: Optional[float] = None,
    smoothed_bull_prob: Optional[float] = None,
    kelly_fraction: Optional[float] = None,
    sizing_method: Optional[str] = None,
    hmm_fit_failed: Optional[bool] = None,
    entry_dollars: Optional[float] = None,
    equity_at_entry: Optional[float] = None,
) -> int:
    with _conn() as conn:
        cur = conn.execute(
            """INSERT INTO signal_log
               (ticker, timestamp, composite_score, signal, action,
                skip_reason, price_at_signal, atr_at_signal,
                hmm_regime, sentiment_score, smoothed_bull_prob,
                kelly_fraction, sizing_method, hmm_fit_failed,
                entry_dollars, equity_at_entry)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                ticker.upper(),
                datetime.utcnow().isoformat(),
                composite_score,
                signal,
                action,
                skip_reason,
                price_at_signal,
                atr_at_signal,
                hmm_regime,
                sentiment_score,
                smoothed_bull_prob,
                kelly_fraction,
                sizing_method,
                int(hmm_fit_failed) if hmm_fit_failed is not None else None,
                entry_dollars,
                equity_at_entry,
            ),
        )
        return cur.lastrowid  # type: ignore[return-value]


def get_signal_log(limit: int = 50) -> list[dict]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM signal_log ORDER BY timestamp DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def update_trailing_stop(signal_id: int, new_stop: float) -> None:
    with _conn() as conn:
        conn.execute(
            "UPDATE signal_log SET current_stop = ?, stop_updated_at = ? WHERE id = ?",
            (new_stop, datetime.utcnow().isoformat(), signal_id),
        )


def get_latest_run_rows() -> list[dict]:
    """All signal_log rows from the most recent signal-job run (same calendar day as
    the latest non-exit row). Read-only; used by the briefing aggregation."""
    with _conn() as conn:
        anchor = conn.execute(
            "SELECT MAX(timestamp) AS m FROM signal_log WHERE action != 'closed'"
        ).fetchone()
        if not anchor or not anchor["m"]:
            return []
        day = anchor["m"][:10]
        rows = conn.execute(
            "SELECT * FROM signal_log WHERE substr(timestamp,1,10) = ? ORDER BY timestamp",
            (day,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_recent_signal_rows_for_ticker(ticker: str, limit: int = 25) -> list[dict]:
    """Most-recent signal_log rows for one ticker (newest first).

    Read-only — used by the decision-trail endpoint to reconstruct the gate-by-gate
    evaluation of the most recent signal-job run. No computation, just a lookup.
    """
    with _conn() as conn:
        rows = conn.execute(
            """SELECT * FROM signal_log
               WHERE ticker = ?
               ORDER BY timestamp DESC LIMIT ?""",
            (ticker.upper(), limit),
        ).fetchall()
    return [dict(r) for r in rows]


def get_last_buy_signal(ticker: str) -> Optional[dict]:
    with _conn() as conn:
        row = conn.execute(
            """SELECT * FROM signal_log
               WHERE ticker = ? AND signal = 'BUY' AND action = 'ordered'
               ORDER BY timestamp DESC LIMIT 1""",
            (ticker.upper(),),
        ).fetchone()
    return dict(row) if row else None


# ── Trade outcomes ────────────────────────────────────────────────────────────

def get_trade_history() -> list[dict]:
    with _conn() as conn:
        rows = conn.execute(
            """SELECT
                   t.id, t.ticker, t.entry_signal_id,
                   t.entry_price, t.exit_price, t.exit_reason,
                   t.return_pct, t.holding_days, t.composite_score_at_entry,
                   t.exit_timestamp,
                   sl.timestamp AS entry_timestamp
               FROM trade_outcomes t
               LEFT JOIN signal_log sl ON t.entry_signal_id = sl.id
               ORDER BY t.exit_timestamp DESC"""
        ).fetchall()
    return [dict(r) for r in rows]


def get_last_n_trades_by_regime(n: int = 50, regime: Optional[str] = None) -> list[dict]:
    """Return last n closed trades, optionally filtered to a specific HMM regime (bull/bear)."""
    with _conn() as conn:
        if regime:
            rows = conn.execute(
                """SELECT * FROM trade_outcomes
                   WHERE regime_at_entry = ?
                   ORDER BY exit_timestamp DESC LIMIT ?""",
                (regime, n),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM trade_outcomes ORDER BY exit_timestamp DESC LIMIT ?", (n,)
            ).fetchall()
    return [dict(r) for r in rows]


# ── Ticker performance ────────────────────────────────────────────────────────

def record_close_transaction(
    ticker: str,
    score: Optional[float],
    exit_reason: str,
    current_price: float,
    entry_price: float,
    return_pct: float,
    holding_days: int,
    entry_signal_id: Optional[int],
    composite_score_at_entry: Optional[float],
    regime_at_entry: Optional[str] = None,
) -> None:
    """Atomically write signal_log SELL + trade_outcomes + ticker_performance in one transaction."""
    now = datetime.utcnow().isoformat()
    is_win = 1.0 if return_pct > 0 else 0.0
    set_exit_at = exit_reason != "sell_signal"
    ticker = ticker.upper()
    with _conn() as conn:
        # If regime not passed, look it up from the BUY signal_log entry
        if regime_at_entry is None and entry_signal_id is not None:
            try:
                row = conn.execute(
                    "SELECT hmm_regime FROM signal_log WHERE id = ?", (entry_signal_id,)
                ).fetchone()
                if row:
                    regime_at_entry = row["hmm_regime"]
            except Exception:
                pass

        conn.execute(
            """INSERT INTO signal_log
               (ticker, timestamp, composite_score, signal, action,
                skip_reason, price_at_signal, atr_at_signal)
               VALUES (?, ?, ?, 'SELL', 'closed', ?, ?, 0.0)""",
            (ticker, now, score, exit_reason, current_price),
        )
        conn.execute(
            """INSERT INTO trade_outcomes
               (ticker, entry_signal_id, entry_price, exit_price, exit_reason,
                return_pct, holding_days, composite_score_at_entry, exit_timestamp,
                regime_at_entry)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (ticker, entry_signal_id, entry_price, current_price, exit_reason,
             return_pct, holding_days, composite_score_at_entry, now, regime_at_entry),
        )
        row = conn.execute(
            "SELECT total_trades, win_rate, avg_return FROM ticker_performance WHERE ticker = ?",
            (ticker,),
        ).fetchone()
        if row:
            old_total = row["total_trades"]
            new_total = old_total + 1
            new_win_rate = (row["win_rate"] * old_total + is_win) / new_total
            new_avg_return = (row["avg_return"] * old_total + return_pct) / new_total
            if set_exit_at:
                conn.execute(
                    """UPDATE ticker_performance
                       SET total_trades=?, win_rate=?, avg_return=?, last_updated=?, last_exit_at=?
                       WHERE ticker=?""",
                    (new_total, new_win_rate, new_avg_return, now, now, ticker),
                )
            else:
                conn.execute(
                    """UPDATE ticker_performance
                       SET total_trades=?, win_rate=?, avg_return=?, last_updated=?
                       WHERE ticker=?""",
                    (new_total, new_win_rate, new_avg_return, now, ticker),
                )
        else:
            last_exit_at = now if set_exit_at else None
            conn.execute(
                """INSERT INTO ticker_performance
                   (ticker, total_trades, win_rate, avg_return, last_updated, last_exit_at)
                   VALUES (?, 1, ?, ?, ?, ?)""",
                (ticker, is_win, return_pct, now, last_exit_at),
            )


def get_ticker_performance(ticker: str) -> Optional[dict]:
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM ticker_performance WHERE ticker = ?", (ticker.upper(),)
        ).fetchone()
    return dict(row) if row else None


# ── System config ─────────────────────────────────────────────────────────────

def get_config(key: str, default: str = "") -> str:
    with _conn() as conn:
        row = conn.execute(
            "SELECT value FROM system_config WHERE key = ?", (key,)
        ).fetchone()
    return row["value"] if row else default


def set_config(key: str, value: str) -> None:
    with _conn() as conn:
        conn.execute(
            """INSERT INTO system_config (key, value, updated_at) VALUES (?, ?, ?)
               ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""",
            (key, value, datetime.utcnow().isoformat()),
        )


# ── Gate stats queries ───────────────────────────────────────────────────────

def count_buy_evaluations(cutoff: str) -> int:
    # Fix 2-G: exclude stop-loss 'closed' rows so ticker-days with only a close don't
    # inflate the evaluated denominator and understate gate rejection rates.
    with _conn() as conn:
        return conn.execute(
            """SELECT COUNT(DISTINCT ticker || date(timestamp))
               FROM signal_log
               WHERE timestamp >= ? AND action != 'closed'""",
            (cutoff,),
        ).fetchone()[0]


def count_gate_rejections(gate: str, cutoff: str) -> int:
    with _conn() as conn:
        return conn.execute(
            """SELECT COUNT(*) FROM signal_log
               WHERE action = 'skipped'
               AND skip_reason LIKE ? AND timestamp >= ?""",
            (f"{gate}%", cutoff),
        ).fetchone()[0]


# ── Analytics queries ─────────────────────────────────────────────────────────

def get_analytics_data() -> dict:
    with _conn() as conn:
        exit_rows = conn.execute(
            """SELECT exit_reason,
                      ROUND(AVG(return_pct), 2) as avg_return,
                      COUNT(*) as count
               FROM trade_outcomes
               GROUP BY exit_reason
               ORDER BY exit_reason"""
        ).fetchall()

        bucket_rows = conn.execute(
            """SELECT
                   CASE
                       WHEN composite_score_at_entry >= 90 THEN '90+'
                       WHEN composite_score_at_entry >= 85 THEN '85-90'
                       WHEN composite_score_at_entry >= 80 THEN '80-85'
                       WHEN composite_score_at_entry >= 75 THEN '75-80'
                   END as bucket,
                   ROUND(AVG(return_pct), 2)                              as avg_return,
                   ROUND(AVG(CASE WHEN return_pct > 0 THEN 1.0 ELSE 0.0 END), 4) as win_rate,
                   COUNT(*) as count
               FROM trade_outcomes
               WHERE composite_score_at_entry IS NOT NULL
                 AND composite_score_at_entry >= 75
               GROUP BY bucket"""
        ).fetchall()

        ticker_rows = conn.execute(
            """SELECT ticker, total_trades,
                      ROUND(win_rate, 4)   as win_rate,
                      ROUND(avg_return, 2) as avg_return
               FROM ticker_performance
               ORDER BY win_rate DESC"""
        ).fetchall()

        total_closed = conn.execute(
            "SELECT COUNT(*) as c FROM trade_outcomes"
        ).fetchone()["c"]

    bucket_order = {"75-80": 0, "80-85": 1, "85-90": 2, "90+": 3}
    by_score_bucket = sorted(
        [dict(r) for r in bucket_rows if r["bucket"] is not None],
        key=lambda x: bucket_order.get(x["bucket"], 99),
    )

    return {
        "by_exit_reason":  [dict(r) for r in exit_rows],
        "by_score_bucket": by_score_bucket,
        "by_ticker":       [dict(r) for r in ticker_rows],
        "total_closed_trades": total_closed,
    }


# ── Diagnostic snapshots ──────────────────────────────────────────────────────

def save_diagnostic(key: str, data: dict) -> None:
    with _conn() as conn:
        conn.execute(
            """INSERT INTO diagnostic_snapshots (key, data, updated_at)
               VALUES (?, ?, ?)
               ON CONFLICT(key) DO UPDATE SET data=excluded.data, updated_at=excluded.updated_at""",
            (key, json.dumps(data), datetime.utcnow().isoformat()),
        )


def load_diagnostic(key: str) -> Optional[dict]:
    with _conn() as conn:
        row = conn.execute(
            "SELECT data FROM diagnostic_snapshots WHERE key = ?", (key,)
        ).fetchone()
        if row:
            try:
                return json.loads(row["data"])
            except json.JSONDecodeError:
                return None
        return None


# ── Kelly sizing queries ──────────────────────────────────────────────────────

def get_trades_for_kelly(ticker: str) -> list[dict]:
    """Return all closed trades for a ticker (return_pct only) for Kelly computation."""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT return_pct FROM trade_outcomes WHERE ticker = ? ORDER BY exit_timestamp DESC",
            (ticker.upper(),),
        ).fetchall()
    return [dict(r) for r in rows]


def get_all_trades_for_kelly() -> list[dict]:
    """Return all closed trades portfolio-wide (return_pct only) for Kelly prior."""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT return_pct FROM trade_outcomes ORDER BY exit_timestamp DESC"
        ).fetchall()
    return [dict(r) for r in rows]


# ── Edge statistics ───────────────────────────────────────────────────────────

def get_edge_stats() -> dict:
    """Aggregate expectancy across all closed trades (rows in trade_outcomes).

    A closed trade is one with a trade_outcomes row (written when a position exits).
    return_pct is stored as a percent. Returns counts and per-trade edge metrics:
    win_rate (as a percentage), avg win/loss percent, expectancy percent per trade,
    and average holding days. All zeroed when there are no closed trades.
    """
    with _conn() as conn:
        rows = conn.execute(
            "SELECT return_pct, holding_days FROM trade_outcomes WHERE return_pct IS NOT NULL"
        ).fetchall()

    n = len(rows)
    if n == 0:
        return {
            "n": 0, "win_rate": 0.0, "avg_win_pct": 0.0, "avg_loss_pct": 0.0,
            "expectancy_pct": 0.0, "avg_hold_days": 0.0,
        }

    returns = [r["return_pct"] for r in rows]
    wins   = [r for r in returns if r > 0]
    losses = [r for r in returns if r <= 0]          # avg_loss_pct stays ≤ 0
    win_frac = len(wins) / n
    avg_win  = sum(wins) / len(wins) if wins else 0.0
    avg_loss = sum(losses) / len(losses) if losses else 0.0
    # Expectancy uses the win *fraction* (0–1); win_rate is returned as a percentage.
    expectancy = win_frac * avg_win + (1 - win_frac) * avg_loss

    holds = [r["holding_days"] for r in rows if r["holding_days"] is not None]
    avg_hold = sum(holds) / len(holds) if holds else 0.0

    return {
        "n": n,
        "win_rate": round(win_frac * 100, 2),
        "avg_win_pct": round(avg_win, 2),
        "avg_loss_pct": round(avg_loss, 2),
        "expectancy_pct": round(expectancy, 2),
        "avg_hold_days": round(avg_hold, 1),
    }
