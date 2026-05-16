"""SQLite persistence layer for watchlist, signal log, and trade outcomes."""
from __future__ import annotations
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
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db() -> None:
    with _conn() as conn:
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
        """)
    _run_migrations()


def _run_migrations() -> None:
    """Add new columns to existing tables; safe to run on every startup."""
    new_columns = [
        ("signal_log",     "ALTER TABLE signal_log ADD COLUMN current_stop REAL"),
        ("signal_log",     "ALTER TABLE signal_log ADD COLUMN stop_updated_at TIMESTAMP"),
        ("trade_outcomes", "ALTER TABLE trade_outcomes ADD COLUMN entry_score REAL"),
    ]
    with _conn() as conn:
        for _, sql in new_columns:
            try:
                conn.execute(sql)
            except Exception:
                pass  # column already exists


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


# ── Signal log ────────────────────────────────────────────────────────────────

def log_signal(
    ticker: str,
    composite_score: Optional[float],
    signal: Optional[str],
    action: str,
    skip_reason: Optional[str],
    price_at_signal: Optional[float],
    atr_at_signal: Optional[float],
) -> int:
    with _conn() as conn:
        cur = conn.execute(
            """INSERT INTO signal_log
               (ticker, timestamp, composite_score, signal, action,
                skip_reason, price_at_signal, atr_at_signal)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                ticker.upper(),
                datetime.utcnow().isoformat(),
                composite_score,
                signal,
                action,
                skip_reason,
                price_at_signal,
                atr_at_signal,
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

def record_trade(
    ticker: str,
    entry_signal_id: Optional[int],
    entry_price: float,
    exit_price: float,
    exit_reason: str,
    return_pct: float,
    holding_days: int,
    composite_score_at_entry: Optional[float],
) -> None:
    with _conn() as conn:
        conn.execute(
            """INSERT INTO trade_outcomes
               (ticker, entry_signal_id, entry_price, exit_price, exit_reason,
                return_pct, holding_days, composite_score_at_entry, exit_timestamp)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                ticker.upper(),
                entry_signal_id,
                entry_price,
                exit_price,
                exit_reason,
                return_pct,
                holding_days,
                composite_score_at_entry,
                datetime.utcnow().isoformat(),
            ),
        )


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
