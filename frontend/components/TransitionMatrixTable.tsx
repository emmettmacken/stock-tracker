"use client";

const STATE_LABELS = ["S.Down", "Down", "Flat", "Up", "S.Up"];

function cellBg(v: number): string {
  if (v >= 0.5)  return "bg-emerald-900 text-emerald-200";
  if (v >= 0.35) return "bg-emerald-950 text-emerald-300";
  if (v >= 0.2)  return "bg-zinc-700 text-zinc-200";
  return "bg-zinc-800 text-zinc-400";
}

export function TransitionMatrixTable({
  matrix,
  currentStateIdx,
  rowObs,
}: {
  matrix: number[][];
  currentStateIdx: number;
  rowObs?: number[];
}) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs border-separate [border-spacing:3px] tabular-nums">
        <thead>
          <tr>
            <th className="px-1.5 py-1 text-zinc-500 font-normal text-left">→</th>
            {STATE_LABELS.map((s) => (
              <th key={s} className="px-1.5 py-1 text-zinc-400 font-semibold text-center">{s}</th>
            ))}
            {rowObs && <th className="px-1.5 py-1 text-zinc-600 font-normal text-center">n</th>}
          </tr>
        </thead>
        <tbody>
          {matrix.map((row, i) => {
            const isCurrent = i === currentStateIdx;
            return (
              <tr key={i}>
                <td className={`px-1.5 py-1 font-semibold whitespace-nowrap ${isCurrent ? "text-sky-300" : "text-zinc-400"}`}>
                  {STATE_LABELS[i]}
                  {isCurrent && <span className="ml-1 text-sky-400 text-[10px]">●</span>}
                </td>
                {row.map((val, j) => (
                  <td
                    key={j}
                    aria-current={isCurrent ? "true" : undefined}
                    className={`px-1.5 py-1 text-center rounded-md ${cellBg(val)} ${
                      isCurrent ? "ring-2 ring-inset ring-sky-400" : ""
                    }`}
                  >
                    {(val * 100).toFixed(1)}%
                  </td>
                ))}
                {rowObs && (
                  <td className={`px-1.5 py-1 text-center ${rowObs[i] >= 15 ? "text-zinc-500" : "text-amber-500"}`}>
                    {rowObs[i]}
                  </td>
                )}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
