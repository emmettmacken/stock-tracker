"use client";
import { SignalData } from "@/lib/types";
import { TransitionMatrixTable } from "./TransitionMatrixTable";
import { StationaryChart } from "./StationaryChart";
import { VolumeHeatmap } from "./VolumeHeatmap";

function EdgeStat({
  label,
  edge,
  ciLow,
  ciHigh,
  positive,
}: {
  label: string;
  edge: number;
  ciLow: number;
  ciHigh: number;
  positive: boolean;
}) {
  const color = edge > 0 && ciLow > 0 ? (positive ? "text-emerald-400" : "text-red-400") : "text-zinc-400";
  return (
    <div className="bg-zinc-800 rounded-lg p-2.5">
      <div className="text-zinc-500 text-xs mb-0.5">{label}</div>
      <div className={`font-semibold text-sm ${color}`}>
        {edge >= 0 ? "+" : ""}{(edge * 100).toFixed(1)}%
      </div>
      <div className="text-zinc-600 text-[10px] mt-0.5">
        CI [{(ciLow * 100).toFixed(1)}%, {(ciHigh * 100).toFixed(1)}%]
      </div>
    </div>
  );
}

export function DetailPanel({ data }: { data: SignalData }) {
  const currentRetIdx = data.current_return_bucket;
  const currentVolIdx = data.current_vol_bucket;

  const rowObs5 = data.row_observations.map((row) => row.reduce((a, b) => a + b, 0));

  return (
    <div className="space-y-4 text-xs">
      {/* Stats grid */}
      <div className="grid grid-cols-2 gap-2">
        <div className="bg-zinc-800 rounded-lg p-2.5">
          <div className="text-zinc-500 mb-0.5">Current State</div>
          <div className="text-zinc-100 font-semibold">{data.current_state}</div>
        </div>
        <div className="bg-zinc-800 rounded-lg p-2.5">
          <div className="text-zinc-500 mb-0.5">Obs at State</div>
          <div className={`font-semibold ${data.n_obs_current_state >= 15 ? "text-emerald-400" : "text-amber-400"}`}>
            {data.n_obs_current_state} transitions
          </div>
        </div>
        <EdgeStat
          label="Bullish Edge"
          edge={data.bullish_edge}
          ciLow={data.bull_edge_ci_low}
          ciHigh={data.bull_edge_ci_high}
          positive={true}
        />
        <EdgeStat
          label="Bearish Edge"
          edge={data.bearish_edge}
          ciLow={data.bear_edge_ci_low}
          ciHigh={data.bear_edge_ci_high}
          positive={false}
        />
      </div>

      {/* Volume impact heatmap */}
      <div>
        <h4 className="font-semibold text-zinc-400 uppercase tracking-wide mb-2">
          P(Bullish Next) by State
          <span className="text-sky-400 normal-case font-normal ml-1">(● = current)</span>
        </h4>
        <VolumeHeatmap
          heatmap={data.bullish_heatmap}
          observations={data.row_observations}
          returnLabels={data.return_labels}
          volLabels={data.vol_labels}
          currentRetIdx={currentRetIdx}
          currentVolIdx={currentVolIdx}
        />
      </div>

      {/* Stationary distribution */}
      <div>
        <h4 className="font-semibold text-zinc-400 uppercase tracking-wide mb-2">
          Stationary Distribution
        </h4>
        <StationaryChart
          distribution={data.stationary_distribution}
          currentStateIdx={currentRetIdx}
        />
      </div>

      {/* 5×5 marginal transition matrix */}
      <div>
        <h4 className="font-semibold text-zinc-400 uppercase tracking-wide mb-2">
          Transition Matrix (return-marginal)
          <span className="text-sky-400 normal-case font-normal ml-1">(● = current row)</span>
        </h4>
        <TransitionMatrixTable
          matrix={data.transition_matrix_5x5}
          currentStateIdx={currentRetIdx}
          rowObs={rowObs5}
        />
      </div>
    </div>
  );
}
