"use client";
import { useState } from "react";
import { SignalData } from "@/lib/types";
import { DetailPanel } from "@/components/DetailPanel";

// Plain-English translation of the Markov matrix, shown above the raw grids.
export function MarkovDetail({ data }: { data: SignalData }) {
  const n = data.n_obs_current_state;
  const lowConfidence = n < 10 || !data.high_confidence;
  // When the read is low-confidence, the colored matrix/heatmap/stationary grids
  // are misleading, so collapse them behind an explicit opt-in.
  const [showDetails, setShowDetails] = useState(false);

  // P(bullish next) for the exact current state (return bucket × vol bucket).
  const upRaw = data.bullish_heatmap?.[data.current_return_bucket]?.[data.current_vol_bucket];
  const upPct = upRaw != null ? Math.round(upRaw * 100) : null;
  const downPct = upPct != null ? 100 - upPct : null;

  return (
    <div className="space-y-4">
      {/* Plain-English summary */}
      <div className="rounded-lg border border-zinc-800 bg-zinc-950/50 p-4 text-sm text-zinc-300 leading-relaxed">
        {upPct != null ? (
          <p>
            Based on{" "}
            <span className="font-semibold text-zinc-100">{n}</span> similar situations over the
            last <span className="font-semibold text-zinc-100">{data.regime_window_size}</span>{" "}
            trading days, this stock moved into a{" "}
            <span className="font-semibold text-emerald-400">bullish state {upPct}%</span> of the
            time and a <span className="font-semibold text-red-400">bearish state {downPct}%</span>{" "}
            of the time on the next move.
          </p>
        ) : (
          <p>
            This stock has been in its current state (
            <span className="font-semibold text-zinc-100">{data.current_state}</span>){" "}
            <span className="font-semibold text-zinc-100">{n}</span> times in the last{" "}
            {data.regime_window_size} trading days.
          </p>
        )}
        <p className="mt-2 text-xs text-zinc-500">
          The confidence interval below shows the range the true edge likely falls in given this
          limited sample — when it crosses 0%, the edge isn&apos;t statistically reliable.
        </p>
        {lowConfidence && (
          <div className="mt-2 inline-flex items-center gap-1.5 text-[11px] font-medium text-amber-400 bg-amber-950/30 border border-amber-800/40 rounded-md px-2 py-1">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
              <path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" />
              <path d="M12 9v4M12 17h.01" />
            </svg>
            Low-confidence read — only {n} observations at this state
          </div>
        )}
      </div>

      {/* Existing raw grids: edges + CI, heatmap, stationary dist, transition matrix.
          On a low-confidence read these are demoted behind a "Show anyway" toggle. */}
      {lowConfidence ? (
        <div>
          <button
            onClick={() => setShowDetails((v) => !v)}
            aria-expanded={showDetails}
            className="inline-flex items-center gap-1.5 text-xs font-medium text-zinc-400 hover:text-zinc-200 bg-zinc-900 border border-zinc-800 rounded-md px-2.5 py-1.5 transition-colors duration-150 ease-out-quart"
          >
            <svg
              width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor"
              strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden
              className={`transition-transform duration-150 ease-out-quart ${showDetails ? "rotate-90" : ""}`}
            >
              <path d="m9 18 6-6-6-6" />
            </svg>
            {showDetails ? "Hide detailed matrix" : "Show anyway"}
          </button>
          {showDetails && (
            <div className="mt-4">
              <DetailPanel data={data} />
            </div>
          )}
        </div>
      ) : (
        <DetailPanel data={data} />
      )}
    </div>
  );
}
