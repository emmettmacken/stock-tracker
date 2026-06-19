"use client";
import { useRouter } from "next/navigation";
import { SnapshotData, Signal } from "@/lib/types";
import { SignalBadge } from "./SignalBadge";
import { ConfidenceBar } from "./ConfidenceBar";
import { FactorScorePill } from "./v3/FactorScorePill";
import { RegimePill } from "./v3/RegimePill";
import { relativeTime } from "@/lib/relativeTime";

interface Props {
  snapshot: SnapshotData;
  onRemove: () => void;
}

export function TickerCard({ snapshot, onRemove }: Props) {
  const router = useRouter();

  const ticker = snapshot.ticker;
  const ready = snapshot.computed_at !== null;
  const factors = snapshot.factors;
  const signal: Signal = snapshot.signal ?? "HOLD";

  const change = snapshot.price_change_pct ?? 0;
  const isPositive = change >= 0;

  function handleCardClick() {
    if (ready) router.push(`/stock/${ticker}`);
  }

  return (
    <div
      className={`bg-zinc-900 border rounded-xl p-4 transition-all duration-200
        ${!ready ? "opacity-70 border-zinc-800" : "border-zinc-800 hover:border-zinc-600 cursor-pointer"}`}
      onClick={handleCardClick}
    >
      {/* Header row */}
      <div className="flex items-start justify-between">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-1 flex-wrap">
            <span className="text-lg font-bold text-white tracking-tight">{ticker}</span>
            {ready ? (
              <>
                <SignalBadge signal={signal} />
                {snapshot.hmm_regime && <RegimePill regime={snapshot.hmm_regime} />}
                {snapshot.composite_score !== null && (
                  <FactorScorePill score={snapshot.composite_score} size="sm" />
                )}
              </>
            ) : (
              <span className="inline-flex items-center gap-1.5 text-[11px] font-medium text-zinc-400">
                <span className="inline-block w-3 h-3 border border-zinc-600 border-t-zinc-400 rounded-full animate-spin" />
                Calculating…
              </span>
            )}
          </div>

          {ready ? (
            <div className="flex items-baseline gap-2">
              <span className="text-2xl font-semibold text-white">
                ${(snapshot.price ?? 0).toFixed(2)}
              </span>
              <span className={`text-sm font-medium ${isPositive ? "text-emerald-400" : "text-red-400"}`}>
                {isPositive ? "+" : ""}{change.toFixed(2)}%
              </span>
            </div>
          ) : (
            <p className="text-xs text-zinc-500">Computing factor scores…</p>
          )}
        </div>

        <button
          className="ml-2 text-zinc-600 hover:text-red-400 transition-colors text-lg leading-none"
          onClick={(e) => { e.stopPropagation(); onRemove(); }}
          title="Remove"
        >
          ×
        </button>
      </div>

      {ready && (
        <>
          {factors && (
            <div className="mt-3">
              <ConfidenceBar confidence={factors.hmm_confidence ?? 0} signal={signal} />
            </div>
          )}

          {/* Staleness indicator — the displayed score can be up to a day old. */}
          <div className="mt-2 flex items-center justify-between text-xs text-zinc-600">
            <span className="inline-flex items-center gap-1" title={`Computed at ${snapshot.computed_at}`}>
              🕒 Updated {relativeTime(snapshot.computed_at)}
            </span>
            <span className="text-zinc-500">View detail →</span>
          </div>
        </>
      )}
    </div>
  );
}
