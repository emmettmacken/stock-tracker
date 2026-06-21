"use client";
import { SizingResult, FactorScoreData } from "@/lib/types";
import { scoreTextColor } from "../FactorScorePill";

interface Props {
  sizing: SizingResult;
  factorData: Record<string, FactorScoreData>;
  method: "kelly" | "vol";
  capital: number;
}

function fmt(n: number, d = 1) {
  return n.toFixed(d);
}

export function AllocationTable({ sizing, factorData, method, capital }: Props) {
  const tickers = sizing.tickers;

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead>
          <tr className="border-b border-zinc-800 text-left">
            <th className="py-2 pr-3 text-zinc-500 font-medium">Ticker</th>
            <th className="py-2 pr-3 text-zinc-500 font-medium">Score</th>
            <th className="py-2 pr-3 text-zinc-500 font-medium">
              {method === "kelly" ? "Kelly Fraction" : "Weight"}
            </th>
            <th className="py-2 pr-3 text-zinc-500 font-medium">Amount</th>
            <th className="py-2 pr-3 text-zinc-500 font-medium">Vol (21d)</th>
            <th className="py-2 text-zinc-500 font-medium">Corr Adj</th>
          </tr>
        </thead>
        <tbody>
          {tickers.map((ticker) => {
            const alloc = sizing.allocations[ticker];
            if (!alloc) return null;
            const fd = factorData[ticker];
            const score = fd?.composite_score ?? null;
            const penalty = alloc.correlation_penalty;
            const hasPenalty = penalty < 0.97;
            const weight = method === "kelly" ? alloc.kelly_fraction : alloc.vol_targeted_weight;
            const dollar = method === "kelly" ? alloc.kelly_dollar : alloc.vol_targeted_dollar;

            return (
              <tr key={ticker} className="border-b border-zinc-800/50 hover:bg-zinc-800/30 transition-colors duration-150 ease-out-quart">
                <td className="py-2.5 pr-3 font-semibold text-zinc-200">{ticker}</td>
                <td className="py-2.5 pr-3">
                  {score !== null ? (
                    <span className={`font-bold tabular-nums ${scoreTextColor(score)}`}>
                      {fmt(score)}
                    </span>
                  ) : (
                    <span className="text-zinc-600">—</span>
                  )}
                </td>
                <td className="py-2.5 pr-3 tabular-nums text-zinc-300">
                  {method === "kelly"
                    ? `${fmt(alloc.kelly_fraction * 100, 1)}%`
                    : `${fmt(alloc.vol_targeted_weight * 100, 1)}%`}
                </td>
                <td className="py-2.5 pr-3 tabular-nums text-zinc-200 font-medium">
                  ${dollar.toLocaleString("en-US", { minimumFractionDigits: 0, maximumFractionDigits: 0 })}
                </td>
                <td className="py-2.5 pr-3 tabular-nums text-zinc-400">
                  {fmt(alloc.realised_vol_21d * 100, 1)}%
                </td>
                <td className="py-2.5">
                  {hasPenalty ? (
                    <span className="text-amber-400 text-[10px] font-medium tabular-nums bg-amber-950/30 border border-amber-800/40 rounded-md px-1.5 py-0.5">
                      −{fmt((1 - penalty) * 100, 0)}%
                    </span>
                  ) : (
                    <span className="text-zinc-700">—</span>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
        <tfoot>
          <tr className="border-t border-zinc-700">
            <td colSpan={2} className="pt-2 text-zinc-500">Total deployed</td>
            <td className="pt-2 tabular-nums text-zinc-300">
              {fmt(
                tickers.reduce((s, t) => {
                  const a = sizing.allocations[t];
                  return s + (method === "kelly" ? (a?.kelly_fraction ?? 0) : (a?.vol_targeted_weight ?? 0));
                }, 0) * 100,
                1
              )}%
            </td>
            <td className="pt-2 tabular-nums text-zinc-200 font-semibold">
              ${tickers.reduce((s, t) => {
                const a = sizing.allocations[t];
                return s + (method === "kelly" ? (a?.kelly_dollar ?? 0) : (a?.vol_targeted_dollar ?? 0));
              }, 0).toLocaleString("en-US", { minimumFractionDigits: 0, maximumFractionDigits: 0 })}
            </td>
            <td colSpan={2} />
          </tr>
        </tfoot>
      </table>
    </div>
  );
}
