"use client";
import { useState, useEffect } from "react";
import { SignalData, FactorScoreData } from "@/lib/types";
import { SignalBadge } from "./SignalBadge";
import { ConfidenceBar } from "./ConfidenceBar";
import { DetailPanel } from "./DetailPanel";
import { BacktestPanel } from "./BacktestPanel";
import { fetchFactors } from "@/lib/api";
import { FactorScorePill } from "./v3/FactorScorePill";
import { FactorsTab } from "./v3/FactorsTab";
import { AltDataTab } from "./v3/AltDataTab";

interface Props {
  data: SignalData;
  loading: boolean;
  error?: string;
  onRemove: () => void;
}

export function TickerCard({ data, loading, error, onRemove }: Props) {
  const [expanded, setExpanded] = useState(false);
  const [tab, setTab] = useState<"signal" | "backtest" | "factors" | "altdata">("signal");
  const [factorData, setFactorData] = useState<FactorScoreData | null>(null);
  const [factorLoading, setFactorLoading] = useState(false);

  useEffect(() => {
    if (!data.ticker || loading || error) return;
    setFactorLoading(true);
    fetchFactors(data.ticker)
      .then(setFactorData)
      .catch(() => {})
      .finally(() => setFactorLoading(false));
  }, [data.ticker]); // eslint-disable-line react-hooks/exhaustive-deps

  const isPositive = data.change_pct >= 0;

  function handleCardClick() {
    if (loading || error) return;
    if (!expanded) {
      setExpanded(true);
    }
  }

  return (
    <div
      className={`bg-zinc-900 border rounded-xl p-4 transition-all duration-200
        ${loading ? "opacity-60" : ""}
        ${expanded ? "border-zinc-600" : "border-zinc-800 hover:border-zinc-600 cursor-pointer"}`}
      onClick={handleCardClick}
    >
      {/* Header row */}
      <div className="flex items-start justify-between">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-1 flex-wrap">
            <span className="text-lg font-bold text-white tracking-tight">{data.ticker}</span>
            {!error && !loading && (
              <>
                <SignalBadge signal={data.signal} />
                <RegimePill regime={data.regime} />
                {!data.high_confidence && (
                  <span className="text-[10px] font-medium text-amber-400 border border-amber-400/40 rounded px-1.5 py-0.5">
                    Low conf
                  </span>
                )}
                {factorLoading && (
                  <span className="inline-block w-3 h-3 border border-zinc-600 border-t-zinc-400 rounded-full animate-spin" />
                )}
                {!factorLoading && factorData && (
                  <FactorScorePill score={factorData.composite_score} size="sm" />
                )}
              </>
            )}
            {loading && (
              <span className="inline-block w-4 h-4 border-2 border-zinc-600 border-t-zinc-400 rounded-full animate-spin" />
            )}
          </div>

          {error ? (
            <p className="text-xs text-red-400">{error}</p>
          ) : (
            <div className="flex items-baseline gap-2">
              <span className="text-2xl font-semibold text-white">${data.price.toFixed(2)}</span>
              <span className={`text-sm font-medium ${isPositive ? "text-emerald-400" : "text-red-400"}`}>
                {isPositive ? "+" : ""}{data.change_pct.toFixed(2)}%
              </span>
            </div>
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

      {!error && !loading && (
        <div className="mt-3">
          <ConfidenceBar confidence={data.confidence} signal={data.signal} />
        </div>
      )}

      {/* Expand / collapse toggle */}
      {!error && !loading && (
        <div
          className="mt-2 text-right text-xs text-zinc-600 cursor-pointer"
          onClick={(e) => { e.stopPropagation(); setExpanded((x) => !x); }}
        >
          {expanded ? "▲ collapse" : "▼ expand"}
        </div>
      )}

      {/* Expanded section with tabs */}
      {expanded && !error && !loading && (
        <div className="mt-3 pt-3 border-t border-zinc-700">
          {/* Tab bar */}
          <div className="flex flex-wrap gap-1 mb-3" onClick={(e) => e.stopPropagation()}>
            {(
              [
                ["signal", "Signal Analysis"],
                ["backtest", "Backtest"],
                ["factors", "Factors"],
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

          {tab === "signal" && <DetailPanel data={data} />}
          {tab === "backtest" && <BacktestPanel ticker={data.ticker} />}
          {tab === "factors" && (
            factorLoading ? (
              <div className="flex items-center gap-2 py-6 justify-center text-zinc-500 text-xs">
                <span className="inline-block w-4 h-4 border-2 border-zinc-600 border-t-zinc-300 rounded-full animate-spin" />
                Loading factor scores…
              </div>
            ) : factorData ? (
              <FactorsTab data={factorData} ticker={data.ticker} />
            ) : (
              <p className="text-zinc-600 text-xs py-4 text-center">
                Factor data unavailable for {data.ticker}.
              </p>
            )
          )}
          {tab === "altdata" && <AltDataTab ticker={data.ticker} />}
        </div>
      )}
    </div>
  );
}

function RegimePill({ regime }: { regime: "bull" | "bear" }) {
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-semibold tracking-wide
      ${regime === "bull" ? "bg-emerald-900/60 text-emerald-300 border border-emerald-700/50" : "bg-red-900/60 text-red-300 border border-red-700/50"}`}>
      {regime === "bull" ? "▲ Bull" : "▼ Bear"}
    </span>
  );
}
