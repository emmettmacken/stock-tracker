"use client";
import { SnapshotData } from "@/lib/types";
import { SignalBadge } from "@/components/SignalBadge";
import { RegimePill } from "@/components/v3/RegimePill";
import { scoreTextColor } from "@/components/v3/FactorScorePill";
import { relativeTime } from "@/lib/relativeTime";

interface Props {
  snapshot: SnapshotData;
  refreshing: boolean;
  onRefresh: () => void;
}

export function StockHeader({ snapshot, refreshing, onRefresh }: Props) {
  const change = snapshot.price_change_pct ?? 0;
  const isPositive = change >= 0;
  const score = snapshot.composite_score ?? 0;

  return (
    <div className="flex flex-col sm:flex-row sm:items-start sm:justify-between gap-4">
      {/* Left: identity + price */}
      <div className="min-w-0">
        <div className="flex items-center gap-3 flex-wrap">
          <h1 className="text-3xl font-bold tracking-tight text-white">{snapshot.ticker}</h1>
          {snapshot.signal && <SignalBadge signal={snapshot.signal} />}
          {snapshot.hmm_regime && <RegimePill regime={snapshot.hmm_regime} />}
        </div>
        <div className="mt-2 flex items-baseline gap-2">
          <span className="text-2xl font-semibold text-white">
            ${(snapshot.price ?? 0).toFixed(2)}
          </span>
          <span className={`text-sm font-medium ${isPositive ? "text-emerald-400" : "text-red-400"}`}>
            {isPositive ? "+" : ""}{change.toFixed(2)}%
          </span>
        </div>
        <div className="mt-2 flex items-center gap-3 text-xs text-zinc-500">
          <span className="inline-flex items-center gap-1" title={`Computed at ${snapshot.computed_at}`}>
            🕒 Updated {relativeTime(snapshot.computed_at)}
          </span>
          <button
            onClick={onRefresh}
            disabled={refreshing}
            className="inline-flex items-center gap-1.5 px-2.5 py-1 text-xs font-medium rounded-md
              bg-zinc-800 text-zinc-300 hover:bg-zinc-700 disabled:opacity-40 transition-colors"
            title="Recompute this ticker now"
          >
            <span className={refreshing ? "animate-spin inline-block" : ""}>⟳</span>
            {refreshing ? "Refreshing…" : "Refresh this ticker"}
          </button>
        </div>
      </div>

      {/* Right: big composite score */}
      <div className="flex items-center gap-3 shrink-0">
        <div className="text-right">
          <div className="text-[10px] uppercase tracking-widest text-zinc-500">Composite</div>
          <div className="text-[10px] text-zinc-600">score / 100</div>
        </div>
        <div
          className={`flex items-center justify-center w-20 h-20 rounded-2xl border-2 ${scoreRingClass(score)}`}
        >
          <span className={`text-3xl font-bold tabular-nums ${scoreTextColor(score)}`}>
            {Math.round(score)}
          </span>
        </div>
      </div>
    </div>
  );
}

function scoreRingClass(score: number) {
  if (score <= 35) return "border-red-700/50 bg-red-950/30";
  if (score <= 64) return "border-amber-700/50 bg-amber-950/30";
  return "border-emerald-700/50 bg-emerald-950/30";
}
