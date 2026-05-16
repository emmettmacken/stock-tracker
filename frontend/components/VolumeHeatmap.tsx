"use client";

const RET_SHORT = ["S.Down", "Down", "Flat", "Up", "S.Up"];

function heatColor(v: number): string {
  // v in [0,1]: red→yellow→green
  if (v >= 0.55) return "bg-emerald-700 text-emerald-100";
  if (v >= 0.48) return "bg-emerald-900 text-emerald-200";
  if (v >= 0.42) return "bg-zinc-700 text-zinc-200";
  if (v >= 0.35) return "bg-red-950 text-red-300";
  return "bg-red-900/60 text-red-200";
}

export function VolumeHeatmap({
  heatmap,
  observations,
  returnLabels,
  volLabels,
  currentRetIdx,
  currentVolIdx,
}: {
  heatmap: number[][];
  observations: number[][];
  returnLabels: string[];
  volLabels: string[];
  currentRetIdx: number;
  currentVolIdx: number;
}) {
  const shortVol = ["Low", "Mid", "High"];

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs border-collapse">
        <thead>
          <tr>
            <th className="px-1.5 py-1 text-zinc-500 font-normal text-left" />
            {shortVol.map((v) => (
              <th key={v} className="px-1.5 py-1 text-zinc-400 font-semibold text-center">{v}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {heatmap.map((row, r) => (
            <tr key={r} className={r === currentRetIdx ? "ring-1 ring-inset ring-sky-500" : ""}>
              <td className="px-1.5 py-1 text-zinc-400 font-semibold whitespace-nowrap">
                {RET_SHORT[r]}
                {r === currentRetIdx && <span className="ml-1 text-sky-400 text-[10px]">●</span>}
              </td>
              {row.map((val, v) => {
                const isCurrent = r === currentRetIdx && v === currentVolIdx;
                const obs = observations[r]?.[v] ?? 0;
                return (
                  <td
                    key={v}
                    className={`px-1.5 py-1.5 text-center rounded relative
                      ${heatColor(val)}
                      ${isCurrent ? "ring-2 ring-sky-400" : ""}`}
                  >
                    <div className="font-semibold">{(val * 100).toFixed(0)}%</div>
                    <div className={`text-[9px] opacity-70 ${obs < 15 ? "text-amber-300" : ""}`}>
                      n={obs}
                    </div>
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
      <p className="text-zinc-600 text-[10px] mt-1.5">
        Cell = P(next day bullish) from that (return, volume) state. n = observations.
      </p>
    </div>
  );
}
