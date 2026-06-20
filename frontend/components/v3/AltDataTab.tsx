"use client";
import { useState, useEffect } from "react";
import { ShortInterestData } from "@/lib/types";
import { fetchShortInterest } from "@/lib/api";
import { SkeletonCard } from "./Skeleton";

function SectionHeader({ title }: { title: string }) {
  return (
    <h4 className="text-[10px] font-semibold text-zinc-500 uppercase tracking-widest mb-2">
      {title}
    </h4>
  );
}

function Unavailable({ reason }: { reason?: string }) {
  return (
    <p className="text-zinc-600 text-xs italic">
      {reason ?? "Not available"}
    </p>
  );
}

function RetryButton({ onClick }: { onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className="text-zinc-500 hover:text-zinc-300 text-[10px] underline mt-1"
    >
      Retry
    </button>
  );
}

function ShortSection({ ticker }: { ticker: string }) {
  const [data, setData] = useState<ShortInterestData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  function load() {
    setLoading(true);
    setError(null);
    fetchShortInterest(ticker)
      .then(setData)
      .catch((e) => setError(e instanceof Error ? e.message : "Failed"))
      .finally(() => setLoading(false));
  }

  useEffect(() => { load(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  if (loading) return <SkeletonCard />;
  if (error) return (
    <div>
      <p className="text-red-400 text-xs">{error}</p>
      <RetryButton onClick={load} />
    </div>
  );
  if (!data?.available) return <Unavailable />;

  const floatPct = data.short_float_pct;
  const isHigh = data.high_short_interest;

  return (
    <div className="space-y-2">
      {isHigh && (
        <div className="flex items-center gap-1.5 text-amber-400 text-[10px] font-medium bg-amber-950/30 border border-amber-800/40 rounded-md px-2 py-1">
          <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
            <path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" />
            <path d="M12 9v4M12 17h.01" />
          </svg>
          High short interest — potential squeeze setup
        </div>
      )}
      <div className="grid grid-cols-3 gap-2">
        <div className="bg-zinc-800/50 rounded-lg p-2.5">
          <div className="text-zinc-500 text-[10px]">Short Float</div>
          <div className={`font-semibold tabular-nums ${isHigh ? "text-amber-400" : "text-zinc-200"}`}>
            {floatPct != null ? `${floatPct.toFixed(1)}%` : "—"}
          </div>
        </div>
        <div className="bg-zinc-800/50 rounded-lg p-2.5">
          <div className="text-zinc-500 text-[10px]">Short Ratio</div>
          <div className="text-zinc-200 font-semibold tabular-nums">
            {data.short_ratio != null ? data.short_ratio.toFixed(1) : "—"}
          </div>
        </div>
        <div className="bg-zinc-800/50 rounded-lg p-2.5">
          <div className="text-zinc-500 text-[10px]">Shares Short</div>
          <div className="text-zinc-200 font-semibold tabular-nums text-[11px]">
            {data.shares_short != null
              ? data.shares_short >= 1e6
                ? `${(data.shares_short / 1e6).toFixed(1)}M`
                : data.shares_short.toLocaleString()
              : "—"}
          </div>
        </div>
      </div>
      {floatPct != null && (
        <div>
          <div className="h-1.5 bg-zinc-800 rounded-full overflow-hidden">
            <div
              className={`h-full rounded-full ${floatPct > 20 ? "bg-amber-500" : "bg-zinc-500"}`}
              style={{ width: `${Math.min(floatPct, 100)}%` }}
            />
          </div>
          <div className="flex justify-between text-[9px] text-zinc-700 mt-0.5">
            <span>0%</span>
            <span>20%</span>
            <span>100%</span>
          </div>
        </div>
      )}
    </div>
  );
}

export function AltDataTab({ ticker }: { ticker: string }) {
  return (
    <div className="space-y-5 text-xs">
      <div>
        <SectionHeader title="Short Interest" />
        <ShortSection ticker={ticker} />
        <p className="text-zinc-600 text-[10px] mt-2 leading-relaxed">
          Share of float sold short. Shown for context only — it is not one of the scoring factors.
          News sentiment and insider activity now appear in the factor breakdown above, where they
          contribute to the composite score.
        </p>
      </div>
    </div>
  );
}
