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
      className={`group bg-zinc-900 border rounded-xl p-4
        transition-[border-color,transform,background-color] duration-200 ease-out-quart
        ${!ready
          ? "opacity-70 border-zinc-800"
          : "border-zinc-800 hover:border-zinc-700 hover:bg-zinc-900/60 hover:-translate-y-px cursor-pointer"}`}
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
              <span className="text-2xl font-semibold text-white tabular-nums tracking-tight">
                ${(snapshot.price ?? 0).toFixed(2)}
              </span>
              <span className={`text-sm font-medium tabular-nums ${isPositive ? "text-emerald-400" : "text-red-400"}`}>
                {isPositive ? "+" : ""}{change.toFixed(2)}%
              </span>
            </div>
          ) : (
            <p className="text-xs text-zinc-500">Computing factor scores…</p>
          )}
        </div>

        <button
          className="-mr-1 -mt-1 ml-2 flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-lg leading-none
            text-zinc-600 hover:text-red-400 hover:bg-zinc-800/70 transition-colors duration-150 ease-out-quart active:scale-95"
          onClick={(e) => { e.stopPropagation(); onRemove(); }}
          title="Remove"
          aria-label={`Remove ${ticker}`}
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
          <div className="mt-2.5 flex items-center justify-between text-xs text-zinc-600">
            <span className="inline-flex items-center gap-1.5" title={`Computed at ${snapshot.computed_at}`}>
              <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                <circle cx="12" cy="12" r="9" />
                <path d="M12 7v5l3 2" />
              </svg>
              Updated {relativeTime(snapshot.computed_at)}
            </span>
            <span className="inline-flex items-center gap-1 text-zinc-500 transition-colors duration-150 ease-out-quart group-hover:text-zinc-300">
              View detail
              <span className="transition-transform duration-150 ease-out-quart group-hover:translate-x-0.5">→</span>
            </span>
          </div>
        </>
      )}
    </div>
  );
}
