"""One-time backfill: split pre-fix trimmed trades into profit_take + remainder legs.

Before the C4 fix (commit aa40b69), a position trimmed at +15% wrote only ONE
trade_outcomes row when the remaining half eventually closed: return_pct described
just the remainder, the banked +15% half was never recorded, and a trimmed trade
could be classified as a single loss. This script retroactively splits those rows
using the `profit_take_half:+X%` entries in signal_log (which carry the trim price
and timestamp), then rebuilds ticker_performance from the corrected table.

Run via the Railway dashboard Console (usual DB-ops process):

    python backfill_profit_take_legs.py            # dry run: prints the plan, mutates nothing
    python backfill_profit_take_legs.py --apply    # backs up both tables, then applies

Safety:
  - Guarded by the system_config marker 'profit_take_backfill_v1_applied' — a second
    --apply run is a hard no-op, matching the _migrate_config_baselines pattern.
  - --apply first snapshots trade_outcomes and ticker_performance to backup tables
    (suffix _backup_pre_pt_backfill) AND JSON exports next to the DB file.
  - Only trades matched UNAMBIGUOUSLY are split: the trim row must sit strictly
    between exactly one BUY 'ordered' entry and exactly one leg-NULL trade_outcomes
    row linked to that entry. Anything ambiguous is reported and left untouched.
  - Rows already carrying a leg (written after the C4 fix) are never touched.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime

import numpy as np

DB_PATH = os.getenv(
    "DB_PATH",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "stock_tracker.db"),
)
MARKER = "profit_take_backfill_v1_applied"
BACKUP_SUFFIX = "_backup_pre_pt_backfill"


def _busdays(start_iso: str, end_iso: str) -> int:
    """Trading days between two ISO timestamps (same convention as
    main._trading_days_between, reimplemented here so the script has no import
    chain into main.py / alpaca / yfinance)."""
    try:
        s = datetime.fromisoformat(start_iso).date()
        e = datetime.fromisoformat(end_iso).date()
        return int(np.busday_count(s, e))
    except Exception:
        return 0


def _fmt_row(r: sqlite3.Row | dict) -> str:
    d = dict(r)
    return (
        f"id={d.get('id')} {d.get('ticker')} leg={d.get('leg')!r} "
        f"entry={d.get('entry_price')} exit={d.get('exit_price')} "
        f"reason={d.get('exit_reason')} ret={d.get('return_pct'):+.2f}% "
        f"hold={d.get('holding_days')}d $={d.get('entry_dollars')} "
        f"exit_ts={d.get('exit_timestamp')}"
    )


def find_splits(conn: sqlite3.Connection) -> tuple[list[dict], list[dict]]:
    """Return (splits, skipped). Each split dict has the trim row, entry row, and
    the final-exit trade_outcomes row it unambiguously belongs to."""
    trims = conn.execute(
        """SELECT * FROM signal_log
           WHERE action = 'closed' AND skip_reason LIKE 'profit_take_half%'
           ORDER BY timestamp"""
    ).fetchall()

    splits: list[dict] = []
    skipped: list[dict] = []
    for trim in trims:
        # Entry: latest ordered BUY for this ticker strictly before the trim.
        entry = conn.execute(
            """SELECT * FROM signal_log
               WHERE ticker = ? AND signal = 'BUY' AND action = 'ordered'
                 AND timestamp < ?
               ORDER BY timestamp DESC LIMIT 1""",
            (trim["ticker"], trim["timestamp"]),
        ).fetchone()
        if entry is None:
            skipped.append({"trim_id": trim["id"], "ticker": trim["ticker"],
                            "why": "no BUY 'ordered' row before trim"})
            continue

        # Final exit: the leg-NULL outcome row linked to that entry, closing after
        # the trim. Post-fix trades already have leg='profit_take'/'remainder' rows
        # and fall out here (0 leg-NULL matches → skipped as already-correct).
        finals = conn.execute(
            """SELECT * FROM trade_outcomes
               WHERE ticker = ? AND entry_signal_id = ? AND leg IS NULL
                 AND exit_timestamp > ?""",
            (trim["ticker"], entry["id"], trim["timestamp"]),
        ).fetchall()
        already_split = conn.execute(
            """SELECT COUNT(*) FROM trade_outcomes
               WHERE entry_signal_id = ? AND leg IS NOT NULL""",
            (entry["id"],),
        ).fetchone()[0]
        if already_split:
            skipped.append({"trim_id": trim["id"], "ticker": trim["ticker"],
                            "why": "entry already has leg rows (post-fix trade)"})
            continue
        if len(finals) != 1:
            skipped.append({"trim_id": trim["id"], "ticker": trim["ticker"],
                            "why": f"ambiguous: {len(finals)} leg-NULL outcome rows "
                                   f"for entry_signal_id={entry['id']} after trim"})
            continue
        final = finals[0]

        trim_price = trim["price_at_signal"]
        entry_price = final["entry_price"]
        if not trim_price or not entry_price or entry_price <= 0:
            skipped.append({"trim_id": trim["id"], "ticker": trim["ticker"],
                            "why": "missing trim/entry price"})
            continue

        # Cross-check the recomputed trim return against the +X% recorded in the
        # skip_reason text; a big mismatch means the rows don't belong together.
        trim_ret = (trim_price - entry_price) / entry_price * 100
        try:
            logged = float(trim["skip_reason"].split(":+")[1].rstrip("%"))
            if abs(logged - trim_ret) > 0.5:
                skipped.append({
                    "trim_id": trim["id"], "ticker": trim["ticker"],
                    "why": f"trim return mismatch: logged +{logged}% vs "
                           f"recomputed {trim_ret:+.2f}% — not splitting",
                })
                continue
        except (IndexError, ValueError):
            pass  # old rows without the +X% suffix: rely on the recomputed value

        splits.append({
            "trim": dict(trim), "entry": dict(entry), "final": dict(final),
            "trim_ret": trim_ret,
            "trim_hold_days": _busdays(entry["timestamp"], trim["timestamp"]),
            "half_dollars": (entry["entry_dollars"] / 2
                             if entry["entry_dollars"] else None),
        })
    return splits, skipped


def rebuild_ticker_performance(conn: sqlite3.Connection) -> list[dict]:
    """Recompute total_trades / win_rate / avg_return per ticker from trade_outcomes.
    last_exit_at is preserved (it drives the re-entry cooldown and is not derivable
    from outcomes: profit_take legs must not arm it). Returns before/after rows."""
    now = datetime.utcnow().isoformat()
    changes = []
    stats = conn.execute(
        """SELECT ticker, COUNT(*) AS n,
                  AVG(CASE WHEN return_pct > 0 THEN 1.0 ELSE 0.0 END) AS wr,
                  AVG(return_pct) AS ar
           FROM trade_outcomes GROUP BY ticker"""
    ).fetchall()
    for s in stats:
        before = conn.execute(
            "SELECT * FROM ticker_performance WHERE ticker = ?", (s["ticker"],)
        ).fetchone()
        conn.execute(
            """INSERT INTO ticker_performance
                   (ticker, total_trades, win_rate, avg_return, last_updated, last_exit_at)
               VALUES (?, ?, ?, ?, ?, NULL)
               ON CONFLICT(ticker) DO UPDATE SET
                   total_trades = excluded.total_trades,
                   win_rate     = excluded.win_rate,
                   avg_return   = excluded.avg_return,
                   last_updated = excluded.last_updated""",
            (s["ticker"], s["n"], s["wr"], s["ar"], now),
        )
        changes.append({
            "ticker": s["ticker"],
            "before": dict(before) if before else None,
            "after": {"total_trades": s["n"], "win_rate": round(s["wr"], 4),
                      "avg_return": round(s["ar"], 4)},
        })
    return changes


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true",
                    help="actually mutate the DB (default is a dry-run preview)")
    ap.add_argument("--db", default=DB_PATH, help=f"DB path (default {DB_PATH})")
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row

    marker = conn.execute(
        "SELECT value FROM system_config WHERE key = ?", (MARKER,)
    ).fetchone()
    if marker:
        print(f"ABORT: backfill already applied at {marker['value']} "
              f"(marker {MARKER!r} set). Nothing to do.")
        return 0

    splits, skipped = find_splits(conn)

    print(f"DB: {os.path.abspath(args.db)}")
    print(f"Trim rows found: {len(splits) + len(skipped)} "
          f"→ {len(splits)} splittable, {len(skipped)} skipped\n")
    for s in skipped:
        print(f"  SKIP trim_id={s['trim_id']} {s['ticker']}: {s['why']}")

    for s in splits:
        t, e, f = s["trim"], s["entry"], s["final"]
        print(f"\n{f['ticker']} (entry_signal_id={e['id']}, "
              f"entry {e['timestamp'][:10]} @ {f['entry_price']}):")
        print(f"  BEFORE: {_fmt_row(f)}")
        print(f"  AFTER : NEW leg='profit_take' exit={t['price_at_signal']} "
              f"ret={s['trim_ret']:+.2f}% hold={s['trim_hold_days']}d "
              f"$={s['half_dollars']} exit_ts={t['timestamp']}")
        print(f"          UPD id={f['id']} → leg='remainder' "
              f"$={s['half_dollars']} (return/exit unchanged)")

    if not args.apply:
        print("\nDRY RUN — no changes made. Re-run with --apply to execute.")
        return 0

    if not splits:
        print("\nNo splittable trades; setting marker so this never re-runs.")
    else:
        # Backups: SQL snapshot tables + JSON exports beside the DB file.
        ts = datetime.utcnow().strftime("%Y%m%dT%H%M%S")
        for table in ("trade_outcomes", "ticker_performance"):
            conn.execute(f"DROP TABLE IF EXISTS {table}{BACKUP_SUFFIX}")
            conn.execute(
                f"CREATE TABLE {table}{BACKUP_SUFFIX} AS SELECT * FROM {table}")
            rows = [dict(r) for r in conn.execute(f"SELECT * FROM {table}")]
            path = os.path.join(os.path.dirname(os.path.abspath(args.db)),
                                f"{table}_backup_{ts}.json")
            with open(path, "w") as fh:
                json.dump(rows, fh, indent=1, default=str)
            print(f"Backed up {table}: table {table}{BACKUP_SUFFIX} + {path}")

        for s in splits:
            t, e, f = s["trim"], s["entry"], s["final"]
            conn.execute(
                """INSERT INTO trade_outcomes
                       (ticker, entry_signal_id, entry_price, exit_price, exit_reason,
                        return_pct, holding_days, composite_score_at_entry,
                        exit_timestamp, regime_at_entry, leg, entry_dollars)
                   VALUES (?, ?, ?, ?, 'profit_take_half', ?, ?, ?, ?, ?, 'profit_take', ?)""",
                (f["ticker"], e["id"], f["entry_price"], t["price_at_signal"],
                 s["trim_ret"], s["trim_hold_days"], f["composite_score_at_entry"],
                 t["timestamp"], f["regime_at_entry"], s["half_dollars"]),
            )
            conn.execute(
                "UPDATE trade_outcomes SET leg = 'remainder', entry_dollars = ? WHERE id = ?",
                (s["half_dollars"], f["id"]),
            )

        print("\nRebuilding ticker_performance from corrected trade_outcomes:")
        for ch in rebuild_ticker_performance(conn):
            b = ch["before"]
            b_str = (f"trades={b['total_trades']} wr={b['win_rate']:.4f} "
                     f"avg={b['avg_return']:.4f}" if b else "(absent)")
            a = ch["after"]
            print(f"  {ch['ticker']}: {b_str} → trades={a['total_trades']} "
                  f"wr={a['win_rate']:.4f} avg={a['avg_return']:.4f}")

    conn.execute(
        """INSERT INTO system_config (key, value, updated_at) VALUES (?, ?, ?)
           ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""",
        (MARKER, datetime.utcnow().isoformat(), datetime.utcnow().isoformat()),
    )
    conn.commit()
    print(f"\nDone: {len(splits)} historical trade(s) split. Marker {MARKER!r} set.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
