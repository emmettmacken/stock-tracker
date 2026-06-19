"use client";
import { VERDICT_BANDS, verdictForScore } from "@/lib/verdict";

export function VerdictBands({ score }: { score: number }) {
  const active = verdictForScore(score);

  return (
    <div className="space-y-3">
      {/* Active verdict callout */}
      <div className={`rounded-xl border p-4 ${active.border} ${active.bg}`}>
        <div className="flex items-center gap-2">
          <span className={`text-lg font-bold ${active.text}`}>{active.label}</span>
          <span className="text-xs text-zinc-500">· composite {Math.round(score)} / 100</span>
        </div>
        <p className="mt-1 text-sm text-zinc-300">{active.explanation}</p>
      </div>

      {/* All bands for reference (Stocky-style verdict scale) */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
        {VERDICT_BANDS.map((b) => {
          const isActive = b.label === active.label;
          return (
            <div
              key={b.label}
              className={`rounded-lg border px-3 py-2 transition-colors ${
                isActive
                  ? `${b.border} ${b.bg}`
                  : "border-zinc-800 bg-zinc-900/40 opacity-60"
              }`}
            >
              <div className={`text-xs font-semibold ${isActive ? b.text : "text-zinc-400"}`}>
                {b.label}
              </div>
              <div className="text-[10px] text-zinc-500 tabular-nums">{b.rangeLabel}</div>
              <div className="mt-1.5 h-1 rounded-full bg-zinc-800 overflow-hidden">
                <div className={`h-full ${isActive ? b.bar : "bg-zinc-700"}`} style={{ width: "100%" }} />
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
