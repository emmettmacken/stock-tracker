"use client";
import { useState, useEffect } from "react";
import { TradeOutcome } from "@/lib/types";
import { fetchTradeHistory } from "@/lib/api";
import { Skeleton } from "@/components/v3/Skeleton";

const EXIT_LABELS: Record<string, string> = {
  sell_signal:    "Signal",
  stop_loss:      "Stop Loss",
  max_hold_exit:  "Time Exit",
};

function ExitBadge({ reason }: { reason: string }) {
  const styles: Record<string, string> = {
    sell_signal:   "text-indigo-300 bg-indigo-900/40 border-indigo-700/40",
    stop_loss:     "text-red-300 bg-red-900/40 border-red-700/40",
    max_hold_exit: "text-amber-300 bg-amber-900/40 border-amber-700/40",
  };
  return (
    <span className={`inline-flex px-1.5 py-0.5 rounded border text-[10px] font-medium ${styles[reason] ?? "text-zinc-400 bg-zinc-800 border-zinc-700"}`}>
      {EXIT_LABELS[reason] ?? reason}
    </span>
  );
}

function StatChip({ label, value, positive }: { label: string; value: string; positive?: boolean }) {
  const color = positive === undefined ? "text-zinc-200" : positive ? "text-emerald-400" : "text-red-400";
  return (
    <div className="bg-zinc-800 rounded-lg px-3 py-2 text-center">
      <div className="text-zinc-500 text-[10px] mb-0.5">{label}</div>
      <div className={`text-sm font-bold tabular-nums ${color}`}>{value}</div>
    </div>
  );
}

export function ClosedTradesPanel() {
  const [trades, setTrades] = useState<TradeOutcome[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  function load() {
    setError(null);
    fetchTradeHistory()
      .then(setTrades)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }

  useEffect(() => { load(); }, []);

  if (loading) return <Skeleton className="h-40 w-full" />;

  if (error) return (
    <div className="text-red-400 text-xs py-4">
      {error}
      <button onClick={load} className="ml-2 underline text-zinc-400 hover:text-white">Retry</button>
    </div>
  );

  if (!trades?.length) return (
    <div className="text-zinc-600 text-sm py-8 text-center">
      No closed trades yet.
    </div>
  );

  // Aggregate stats
  const avgRet = trades.reduce((s, t) => s + t.return_pct, 0) / trades.length;
  const winRate = (trades.filter((t) => t.return_pct > 0).length / trades.length) * 100;
  const avgHold = trades.reduce((s, t) => s + t.holding_days, 0) / trades.length;

  function fmtDate(ts: string | null) {
    if (!ts) return "—";
    try { return new Date(ts + "Z").toLocaleDateString(); } catch { return ts.slice(0, 10); }
  }

  return (
    <div className="space-y-4 text-xs">
      {/* Aggregate stats */}
      <div className="grid grid-cols-3 gap-2">
        <StatChip label="Avg Return / Trade" value={`${avgRet >= 0 ? "+" : ""}${avgRet.toFixed(2)}%`} positive={avgRet >= 0} />
        <StatChip label="Win Rate" value={`${winRate.toFixed(0)}%`} positive={winRate >= 50} />
        <StatChip label="Avg Hold" value={`${avgHold.toFixed(1)}d`} />
      </div>

      {/* Trades table */}
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-zinc-800 text-left">
              {["Ticker", "Entry", "Exit", "Return", "Exit Reason", "Hold", "Score@Entry"].map((h) => (
                <th key={h} className="py-2 pr-4 text-zinc-500 font-medium whitespace-nowrap">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {trades.map((t) => (
              <tr key={t.id} className="border-b border-zinc-800/50 hover:bg-zinc-800/30">
                <td className="py-2.5 pr-4 font-bold text-zinc-200">{t.ticker}</td>
                <td className="py-2.5 pr-4 tabular-nums text-zinc-400">{fmtDate(t.entry_timestamp)}</td>
                <td className="py-2.5 pr-4 tabular-nums text-zinc-400">{fmtDate(t.exit_timestamp)}</td>
                <td className={`py-2.5 pr-4 tabular-nums font-semibold ${t.return_pct >= 0 ? "text-emerald-400" : "text-red-400"}`}>
                  {t.return_pct >= 0 ? "+" : ""}{t.return_pct.toFixed(2)}%
                </td>
                <td className="py-2.5 pr-4"><ExitBadge reason={t.exit_reason} /></td>
                <td className="py-2.5 pr-4 tabular-nums text-zinc-400">{t.holding_days}d</td>
                <td className="py-2.5 tabular-nums text-zinc-500">
                  {t.composite_score_at_entry != null ? t.composite_score_at_entry.toFixed(1) : "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
