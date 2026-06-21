"use client";
import { useEffect, useMemo, useState } from "react";
import { PricePoint, TradeOutcome } from "@/lib/types";
import { fetchPriceHistory, fetchTradeHistory } from "@/lib/api";
import { Period, periodCutoff } from "@/lib/period";

// The trade log stores each closed trade's return % but never the entry dollar
// amount (position sizing is computed live at order time and isn't persisted),
// so dollar P&L can't be reconstructed. Returns below are therefore percentages,
// which are accurate regardless of position size.

function fmtPct(n: number): string {
  return `${n >= 0 ? "+" : ""}${n.toFixed(1)}%`;
}

function Stat({
  label,
  value,
  sub,
  tone = "neutral",
}: {
  label: string;
  value: React.ReactNode;
  sub?: string;
  tone?: "neutral" | "pos" | "neg";
}) {
  const valueColor =
    tone === "pos" ? "text-emerald-400" : tone === "neg" ? "text-red-400" : "text-zinc-100";
  return (
    <div className="rounded-lg border border-zinc-800 bg-zinc-950/40 p-3">
      <div className="text-[10px] uppercase tracking-wider text-zinc-500">{label}</div>
      <div className={`mt-1 text-lg font-semibold tabular-nums ${valueColor}`}>{value}</div>
      {sub && <div className="mt-0.5 text-[11px] text-zinc-500 tabular-nums">{sub}</div>}
    </div>
  );
}

export function TickerAnalytics({ ticker, period }: { ticker: string; period: Period }) {
  const [points, setPoints] = useState<PricePoint[] | null>(null);
  const [trades, setTrades] = useState<TradeOutcome[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setError(null);
    setPoints(null);
    setTrades(null);
    fetchPriceHistory(ticker, 760)
      .then((d) => { if (!cancelled) setPoints(d.points); })
      .catch((e) => { if (!cancelled) setError(e instanceof Error ? e.message : "Failed to load"); });
    fetchTradeHistory()
      .then((all) => { if (!cancelled) setTrades(all.filter((t) => t.ticker === ticker)); })
      .catch(() => { if (!cancelled) setTrades([]); });
    return () => { cancelled = true; };
  }, [ticker]);

  // Anchor the window to the most recent price point so it matches the chart.
  const cutoff = useMemo(() => {
    const anchor = points?.length ? points[points.length - 1].date : new Date().toISOString().slice(0, 10);
    return periodCutoff(period, anchor);
  }, [points, period]);

  // Closed trades whose exit lands inside the selected window.
  const windowTrades = useMemo(() => {
    if (!trades) return null;
    if (!cutoff) return trades;
    return trades.filter((t) => t.exit_timestamp.slice(0, 10) >= cutoff);
  }, [trades, cutoff]);

  // Buy & hold over the same window: first → last close inside it (price-based %).
  const bahPct = useMemo<number | null>(() => {
    if (!points?.length) return null;
    const inWindow = cutoff ? points.filter((p) => p.date >= cutoff) : points;
    if (inWindow.length < 2) return null;
    const first = inWindow[0].close;
    const last = inWindow[inWindow.length - 1].close;
    if (!first) return null;
    return (last / first - 1) * 100;
  }, [points, cutoff]);

  // Realized performance: compound each closed trade's return % (size-independent).
  const realized = useMemo(() => {
    if (!windowTrades || windowTrades.length === 0) return null;
    const wins = windowTrades.filter((t) => t.return_pct > 0).length;
    const growth = windowTrades.reduce((acc, t) => acc * (1 + t.return_pct / 100), 1);
    const pct = (growth - 1) * 100;
    const returns = windowTrades.map((t) => t.return_pct);
    const avgHold =
      windowTrades.reduce((s, t) => s + (t.holding_days ?? 0), 0) / windowTrades.length;
    return {
      count: windowTrades.length,
      winRate: (wins / windowTrades.length) * 100,
      wins,
      pct,
      best: Math.max(...returns),
      worst: Math.min(...returns),
      avgHold,
    };
  }, [windowTrades]);

  const periodLabel = period === "Max" ? "all time" : `the ${period} window`;

  if (error) {
    return <p className="text-red-400 text-xs py-3 text-center">{error}</p>;
  }
  if (!windowTrades || !points) {
    return (
      <div className="flex items-center gap-2 py-8 justify-center text-zinc-500 text-xs">
        <span className="inline-block w-4 h-4 border-2 border-zinc-600 border-t-zinc-300 rounded-full animate-spin" />
        Loading analytics…
      </div>
    );
  }

  const noTrades = realized === null;

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 sm:grid-cols-3 gap-2.5">
        {/* Win rate */}
        {noTrades ? (
          <Stat label="Win rate" value={<span className="text-zinc-500 text-sm font-normal">No trades</span>} />
        ) : (
          <Stat
            label="Win rate"
            value={`${realized.winRate.toFixed(0)}%`}
            sub={`${realized.wins}/${realized.count} trades won`}
          />
        )}

        {/* Realized return — compounded trade returns (no dollar figure: sizes aren't stored). */}
        {noTrades ? (
          <Stat label="Realized return" value={<span className="text-zinc-500 text-sm font-normal">No trades</span>} />
        ) : (
          <Stat
            label="Realized return"
            value={fmtPct(realized.pct)}
            sub="compounded across trades"
            tone={realized.pct >= 0 ? "pos" : "neg"}
          />
        )}

        {/* Buy & hold over the same window — price-based %, shown even with no trades. */}
        {bahPct != null ? (
          <Stat
            label="Buy & hold"
            value={fmtPct(bahPct)}
            sub="same window"
            tone={bahPct >= 0 ? "pos" : "neg"}
          />
        ) : (
          <Stat label="Buy & hold" value={<span className="text-zinc-500 text-sm font-normal">No data</span>} />
        )}

        {/* Secondary per-ticker stats — only meaningful when trades exist in the window. */}
        {!noTrades && (
          <>
            <Stat label="Trades" value={String(realized.count)} sub={`avg hold ${realized.avgHold.toFixed(0)}d`} />
            <Stat
              label="Best trade"
              value={fmtPct(realized.best)}
              tone={realized.best >= 0 ? "pos" : "neg"}
            />
            <Stat
              label="Worst trade"
              value={fmtPct(realized.worst)}
              tone={realized.worst >= 0 ? "pos" : "neg"}
            />
          </>
        )}
      </div>

      <p className="text-[11px] text-zinc-600 leading-relaxed">
        {noTrades
          ? `No closed trades for ${ticker} in ${periodLabel}. Buy & hold reflects price action over the same window.`
          : `Realized return compounds this system's closed ${ticker} trades over ${periodLabel}; buy & hold is the price change over the same window, so both compare on equal footing.`}
      </p>
    </div>
  );
}
