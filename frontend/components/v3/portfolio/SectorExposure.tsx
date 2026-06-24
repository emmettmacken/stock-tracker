"use client";
import { useEffect, useState } from "react";
import { SectorExposure } from "@/lib/types";
import { fetchSectorExposure } from "@/lib/api";

// Current sector allocation of live Alpaca positions. Read-only — reuses the cached
// _get_sector lookup the concentration gate already relies on.
export function SectorExposurePanel() {
  const [data, setData] = useState<SectorExposure | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    fetchSectorExposure()
      .then((d) => { if (!cancelled) setData(d); })
      .catch(() => { if (!cancelled) setData(null); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, []);

  if (loading) {
    return <div className="h-20 rounded-xl bg-zinc-800/40 animate-pulse" />;
  }

  if (!data || !data.available) {
    return (
      <p className="text-xs text-zinc-500">
        Live position sectors unavailable{data?.error ? ` — ${data.error}` : " — Alpaca not connected"}.
      </p>
    );
  }

  if (data.total_positions === 0) {
    return <p className="text-xs text-zinc-500">No open positions to show sector exposure for.</p>;
  }

  return (
    <div className="space-y-2.5">
      {data.sectors.map((s) => {
        const tone = s.at_cap
          ? "bg-red-500"
          : s.near_cap
          ? "bg-amber-500"
          : "bg-emerald-500";
        return (
          <div key={s.sector} className="flex items-center gap-3">
            <span className="w-32 shrink-0 truncate text-xs text-zinc-300" title={s.sector}>
              {s.sector}
            </span>
            <div className="flex-1 h-6 rounded bg-zinc-800/40 overflow-hidden relative">
              <div className={`h-full ${tone} rounded`} style={{ width: `${s.pct}%` }} />
              <span className="absolute inset-y-0 left-2 flex items-center text-[10px] text-zinc-300 tabular-nums">
                {s.tickers.join(", ")}
              </span>
            </div>
            <span className="w-40 shrink-0 text-right text-[11px] leading-tight tabular-nums">
              <span className="block">
                <span className="text-zinc-300 font-medium">{s.pct.toFixed(1)}%</span>
                <span className="text-zinc-500"> of invested capital</span>
              </span>
              <span className="block">
                <span className="text-zinc-300 font-medium">{s.count} of {data.max_per_sector}</span>
                <span className="text-zinc-500"> sector slots</span>
              </span>
            </span>
            {(s.at_cap || s.near_cap) && (
              <span
                className={`shrink-0 rounded px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wide ${
                  s.at_cap ? "bg-red-500/15 text-red-400" : "bg-amber-500/15 text-amber-400"
                }`}
              >
                {s.at_cap ? "At cap" : "Near cap"}
              </span>
            )}
          </div>
        );
      })}
      <p className="text-[10px] text-zinc-600 pt-1">
        Concentration cap: {data.max_per_sector} open positions per sector. Sectors at or near the
        cap can&apos;t take new entries until a position closes.
      </p>
    </div>
  );
}
