"use client";
import { SignalData } from "@/lib/types";
import { DetailPanel } from "@/components/DetailPanel";

// Plain-English translation of the Markov matrix, shown above the raw grids.
export function MarkovDetail({ data }: { data: SignalData }) {
  const n = data.n_obs_current_state;
  const lowConfidence = n < 15 || !data.high_confidence;

  // P(bullish next) for the exact current state (return bucket × vol bucket).
  const upRaw = data.bullish_heatmap?.[data.current_return_bucket]?.[data.current_vol_bucket];
  const upPct = upRaw != null ? Math.round(upRaw * 100) : null;
  const downPct = upPct != null ? 100 - upPct : null;

  return (
    <div className="space-y-4">
      {/* Plain-English summary */}
      <div className="rounded-lg border border-zinc-800 bg-zinc-900/40 p-4 text-sm text-zinc-300 leading-relaxed">
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
          <div className="mt-2 inline-flex items-center gap-1.5 text-[11px] font-medium text-amber-400 bg-amber-950/30 border border-amber-800/40 rounded px-2 py-1">
            ⚠ Low-confidence read — only {n} observations at this state
          </div>
        )}
      </div>

      {/* Existing raw grids: edges + CI, heatmap, stationary dist, transition matrix */}
      <DetailPanel data={data} />
    </div>
  );
}
