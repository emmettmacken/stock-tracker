"use client";
import { useState, useEffect } from "react";
import { SnapshotData, SignalData, Signal } from "@/lib/types";
import { SignalBadge } from "./SignalBadge";
import { ConfidenceBar } from "./ConfidenceBar";
import { DetailPanel } from "./DetailPanel";
import { BacktestPanel } from "./BacktestPanel";
import { fetchSignal, refreshTicker } from "@/lib/api";
import { FactorScorePill } from "./v3/FactorScorePill";
import { FactorsTab } from "./v3/FactorsTab";
import { AltDataTab } from "./v3/AltDataTab";
import { relativeTime } from "@/lib/relativeTime";

interface Props {
  snapshot: SnapshotData;
  onRemove: () => void;
  onRefreshed: (snap: SnapshotData) => void;
}

export function TickerCard({ snapshot, onRemove, onRefreshed }: Props) {
  const [expanded, setExpanded] = useState(false);
  const [tab, setTab] = useState<"signal" | "backtest" | "factors" | "altdata">("factors");
  const [refreshing, setRefreshing] = useState(false);

  // Signal Analysis (Markov matrices) is the one tab that still needs a live read.
  // It is fetched lazily — only when the user opens that tab — never on homepage mount.
  const [signalData, setSignalData] = useState<SignalData | null>(null);
  const [signalLoading, setSignalLoading] = useState(false);
  const [signalError, setSignalError] = useState<string | null>(null);

  const ticker = snapshot.ticker;
  const ready = snapshot.computed_at !== null;
  const factors = snapshot.factors;
  const signal: Signal = snapshot.signal ?? "HOLD";

  useEffect(() => {
    if (!expanded || tab !== "signal" || signalData || signalLoading) return;
    setSignalLoading(true);
    setSignalError(null);
    fetchSignal(ticker)
      .then(setSignalData)
      .catch((e) => setSignalError(e instanceof Error ? e.message : "Failed to load"))
      .finally(() => setSignalLoading(false));
  }, [expanded, tab, ticker, signalData, signalLoading]);

  async function handleRefresh() {
    setRefreshing(true);
    try {
      const fresh = await refreshTicker(ticker);
      onRefreshed(fresh);
      // Drop the cached Markov read so the Signal tab re-fetches if reopened.
      setSignalData(null);
    } catch {
      /* leave the stale snapshot in place */
    } finally {
      setRefreshing(false);
    }
  }

  const change = snapshot.price_change_pct ?? 0;
  const isPositive = change >= 0;

  function handleCardClick() {
    if (!ready) return;
    if (!expanded) setExpanded(true);
  }

  return (
    <div
      className={`bg-zinc-900 border rounded-xl p-4 transition-all duration-200
        ${!ready ? "opacity-70" : ""}
        ${expanded ? "border-zinc-600" : "border-zinc-800 hover:border-zinc-600 cursor-pointer"}`}
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
            <span
              className="cursor-pointer hover:text-zinc-400"
              onClick={(e) => { e.stopPropagation(); setExpanded((x) => !x); }}
            >
              {expanded ? "▲ collapse" : "▼ expand"}
            </span>
          </div>
        </>
      )}

      {/* Expanded section with tabs */}
      {expanded && ready && (
        <div className="mt-3 pt-3 border-t border-zinc-700" onClick={(e) => e.stopPropagation()}>
          <div className="flex items-center justify-between mb-3">
            <div className="flex flex-wrap gap-1">
              {(
                [
                  ["factors", "Factors"],
                  ["signal", "Signal Analysis"],
                  ["backtest", "Backtest"],
                  ["altdata", "Alt Data"],
                ] as const
              ).map(([t, label]) => (
                <button
                  key={t}
                  onClick={() => setTab(t)}
                  className={`px-3 py-1 rounded-md text-xs font-medium transition-colors
                    ${tab === t ? "bg-zinc-700 text-white" : "text-zinc-500 hover:text-zinc-300"}`}
                >
                  {label}
                </button>
              ))}
            </div>
            <button
              onClick={handleRefresh}
              disabled={refreshing}
              className="flex items-center gap-1.5 px-2.5 py-1 text-xs font-medium rounded-md
                bg-zinc-800 text-zinc-300 hover:bg-zinc-700 disabled:opacity-40 transition-colors shrink-0"
              title="Recompute this ticker now"
            >
              <span className={refreshing ? "animate-spin inline-block" : ""}>⟳</span>
              {refreshing ? "Refreshing…" : "Refresh this ticker"}
            </button>
          </div>

          {tab === "factors" && (
            factors ? (
              <FactorsTab data={factors} ticker={ticker} />
            ) : (
              <p className="text-zinc-600 text-xs py-4 text-center">
                Factor data unavailable for {ticker}.
              </p>
            )
          )}
          {tab === "signal" && (
            signalLoading ? (
              <div className="flex items-center gap-2 py-6 justify-center text-zinc-500 text-xs">
                <span className="inline-block w-4 h-4 border-2 border-zinc-600 border-t-zinc-300 rounded-full animate-spin" />
                Loading signal analysis…
              </div>
            ) : signalError ? (
              <p className="text-red-400 text-xs py-4 text-center">{signalError}</p>
            ) : signalData ? (
              <DetailPanel data={signalData} />
            ) : null
          )}
          {tab === "backtest" && <BacktestPanel ticker={ticker} />}
          {tab === "altdata" && <AltDataTab ticker={ticker} />}
        </div>
      )}
    </div>
  );
}

function RegimePill({ regime }: { regime: "bull" | "bear" | "transition" }) {
  const cfg = {
    bull:       { cls: "bg-emerald-900/60 text-emerald-300 border-emerald-700/50", label: "▲ Bull" },
    bear:       { cls: "bg-red-900/60 text-red-300 border-red-700/50",            label: "▼ Bear" },
    transition: { cls: "bg-zinc-800 text-zinc-300 border-zinc-600",               label: "↔ Transition" },
  }[regime];
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-semibold tracking-wide border ${cfg.cls}`}>
      {cfg.label}
    </span>
  );
}
