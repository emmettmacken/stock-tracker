"use client";
import { useEffect, useState } from "react";
import { EdgeStats as EdgeStatsData } from "@/lib/types";
import { fetchEdgeStats } from "@/lib/api";
import { StatCard } from "./StatCard";
import { Skeleton } from "@/components/v3/Skeleton";
import { fmtPctSigned } from "@/lib/format";

// Aggregate expectancy across all closed trades. Purely additive read-only panel —
// shown beneath the Open Positions table on the Portfolio page.
export function EdgeStats() {
  const [stats, setStats] = useState<EdgeStatsData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchEdgeStats()
      .then((d) => { if (!cancelled) setStats(d); })
      .catch((e) => { if (!cancelled) setError(e instanceof Error ? e.message : "Failed to load edge stats"); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, []);

  return (
    <div className="space-y-4">
      <h2 className="text-sm font-semibold text-zinc-100 tracking-tight">Edge Statistics</h2>

      {loading && !stats ? (
        <div className="grid grid-cols-2 lg:grid-cols-3 gap-3">
          {[0, 1, 2, 3, 4, 5].map((i) => <Skeleton key={i} className="h-[68px] w-full rounded-xl" />)}
        </div>
      ) : error ? (
        <div className="text-red-400 text-xs">{error}</div>
      ) : !stats || stats.n === 0 ? (
        <div className="bg-zinc-900 border border-zinc-800 rounded-xl px-4 py-8 text-center text-zinc-600 text-sm">
          No closed trades yet.
        </div>
      ) : (
        <>
          <div className="grid grid-cols-2 lg:grid-cols-3 gap-3">
            <StatCard label="Closed Trades" value={stats.n} />
            <StatCard label="Win Rate" value={`${stats.win_rate.toFixed(1)}%`} />
            <StatCard label="Avg Win" value={fmtPctSigned(stats.avg_win_pct)} valueClass="text-emerald-400" />
            <StatCard label="Avg Loss" value={fmtPctSigned(stats.avg_loss_pct)} valueClass="text-red-400" />
            <StatCard
              label="Expectancy / trade"
              value={fmtPctSigned(stats.expectancy_pct)}
              valueClass={stats.expectancy_pct >= 0 ? "text-emerald-400" : "text-red-400"}
            />
            <StatCard label="Avg Hold" value={`${stats.avg_hold_days} days`} />
          </div>

          {stats.low_sample && (
            <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-[11px] leading-relaxed text-amber-300">
              ⚠ Sample size too small to draw conclusions (n &lt; 10). Edge will become
              clearer after more closed trades.
            </div>
          )}
        </>
      )}
    </div>
  );
}
