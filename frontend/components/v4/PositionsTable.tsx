"use client";
import { useState, useEffect } from "react";
import { PaperPosition } from "@/lib/types";
import { fetchPaperPositions } from "@/lib/api";
import { scoreTextColor } from "@/components/v3/FactorScorePill";
import { Skeleton } from "@/components/v3/Skeleton";

function fmt(n: number, d = 2) { return n.toFixed(d); }

export function PositionsTable() {
  const [positions, setPositions] = useState<PaperPosition[] | null>(null);
  const [unavailable, setUnavailable] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);

  function load() {
    fetchPaperPositions()
      .then((d) => {
        if (!d.available) { setUnavailable(d.error ?? "Not connected"); return; }
        setPositions(d.positions ?? []);
        setLastUpdated(new Date());
      })
      .catch((e) => setUnavailable(e.message))
      .finally(() => setLoading(false));
  }

  useEffect(() => {
    load();
    const id = setInterval(load, 60_000);
    return () => clearInterval(id);
  }, []);

  if (loading) return <Skeleton className="h-32 w-full" />;

  if (unavailable) {
    return (
      <p className="text-zinc-600 text-xs py-4">
        Positions unavailable — {unavailable}
      </p>
    );
  }

  if (!positions?.length) {
    return (
      <div className="text-center py-8 text-zinc-600 text-sm">
        No open positions.
        <div className="text-[10px] mt-1 text-zinc-700">
          Positions will appear here once the signal job places orders.
        </div>
      </div>
    );
  }

  return (
    <div>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-zinc-800 text-left">
              {["Ticker", "Entry", "Current", "P&L", "Score", "ATR Stop", "Days", "Value"].map((h) => (
                <th key={h} className="py-2 pr-4 text-zinc-500 font-medium whitespace-nowrap">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {positions.map((p) => {
              const pnlPos = p.pnl_pct >= 0;
              const atRisk = p.atr_stop != null && p.current_price < p.atr_stop * 1.05;
              return (
                <tr key={p.ticker} className="border-b border-zinc-800/50 hover:bg-zinc-800/30">
                  <td className="py-2.5 pr-4 font-bold text-zinc-200">{p.ticker}</td>
                  <td className="py-2.5 pr-4 tabular-nums text-zinc-400">${fmt(p.entry_price)}</td>
                  <td className="py-2.5 pr-4 tabular-nums text-zinc-200">${fmt(p.current_price)}</td>
                  <td className={`py-2.5 pr-4 tabular-nums font-semibold ${pnlPos ? "text-emerald-400" : "text-red-400"}`}>
                    {pnlPos ? "+" : ""}{fmt(p.pnl_pct)}%
                  </td>
                  <td className="py-2.5 pr-4">
                    {p.composite_score != null ? (
                      <span className={`font-bold tabular-nums ${scoreTextColor(p.composite_score)}`}>
                        {fmt(p.composite_score, 1)}
                      </span>
                    ) : <span className="text-zinc-700">—</span>}
                  </td>
                  <td className={`py-2.5 pr-4 tabular-nums ${atRisk ? "text-amber-400" : "text-zinc-500"}`}>
                    {p.atr_stop != null ? `$${fmt(p.atr_stop)}` : "—"}
                    {atRisk && <span className="ml-1 text-amber-500 text-[9px]">⚠ near stop</span>}
                  </td>
                  <td className={`py-2.5 pr-4 tabular-nums ${p.days_held > 18 ? "text-amber-400" : "text-zinc-400"}`}>
                    {p.days_held}d
                    {p.days_held > 18 && <span className="ml-1 text-amber-500 text-[9px]">⚠ expiring</span>}
                  </td>
                  <td className="py-2.5 tabular-nums text-zinc-300">
                    ${p.market_value.toLocaleString("en-IE", { maximumFractionDigits: 0 })}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="text-zinc-700 text-[10px] mt-2">
        {lastUpdated ? `Last updated ${lastUpdated.toLocaleTimeString()}` : ""} · auto-refreshes every 60s
      </p>
    </div>
  );
}
