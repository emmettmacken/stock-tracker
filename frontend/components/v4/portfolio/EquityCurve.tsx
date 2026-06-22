"use client";
import { useEffect, useMemo, useState } from "react";
import {
  AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer,
} from "recharts";
import { PortfolioHistoryPoint } from "@/lib/types";
import { fetchPortfolioHistory } from "@/lib/api";
import { fmtUSD, fmtUSDSigned, fmtPctSigned } from "@/lib/format";

// Selector labels → backend period param. "YTD" requests a full year then slices to the
// calendar year client-side (the backend exposes 1W/1M/3M/6M/1Y/all, not YTD).
const RANGES = ["1W", "1M", "3M", "6M", "YTD", "1Y", "All"] as const;
type Range = (typeof RANGES)[number];

const BACKEND_PERIOD: Record<Range, string> = {
  "1W": "1W", "1M": "1M", "3M": "3M", "6M": "6M", YTD: "1Y", "1Y": "1Y", All: "all",
};

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
function fmtDate(iso: string): string {
  const d = new Date(iso);
  return `${MONTHS[d.getUTCMonth()]} ${d.getUTCDate()}`;
}

interface Row {
  ts: string;
  label: string;
  equity: number;
}

export function EquityCurve() {
  const [range, setRange] = useState<Range>("All");
  const [points, setPoints] = useState<PortfolioHistoryPoint[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchPortfolioHistory(BACKEND_PERIOD[range])
      .then((d) => {
        if (cancelled) return;
        if (!d.available) { setError(d.error ?? "Equity history unavailable"); setPoints(null); }
        else setPoints(d.points ?? []);
      })
      .catch((e) => { if (!cancelled) setError(e instanceof Error ? e.message : "Failed to load"); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [range]);

  // Slice to the calendar year for YTD (backend returned a full year).
  const rows = useMemo<Row[]>(() => {
    if (!points) return [];
    let src = points;
    if (range === "YTD") {
      const yearStart = `${new Date().getUTCFullYear()}-01-01`;
      src = points.filter((p) => p.timestamp.slice(0, 10) >= yearStart);
    }
    return src
      .filter((p) => p.equity != null)
      .map((p) => ({ ts: p.timestamp, label: fmtDate(p.timestamp), equity: p.equity }));
  }, [points, range]);

  // Total return $ and % across the visible window (first → last equity).
  const change = useMemo(() => {
    if (rows.length < 2) return null;
    const first = rows[0].equity;
    const last = rows[rows.length - 1].equity;
    if (!first) return null;
    return { abs: last - first, pct: (last / first - 1) * 100 };
  }, [rows]);

  const yDomain = useMemo<[number, number] | undefined>(() => {
    if (!rows.length) return undefined;
    const vals = rows.map((r) => r.equity);
    const lo = Math.min(...vals), hi = Math.max(...vals);
    const pad = (hi - lo) * 0.08 || hi * 0.02;
    return [lo - pad, hi + pad];
  }, [rows]);

  const up = (change?.abs ?? 0) >= 0;
  const stroke = up ? "#10b981" : "#ef4444";

  return (
    <div className="bg-zinc-900 border border-zinc-800 rounded-xl p-4 sm:p-5 space-y-4">
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <div>
          <h2 className="text-sm font-semibold text-zinc-100 tracking-tight">Equity Curve</h2>
          {change != null ? (
            <p className="mt-1 text-sm font-medium tabular-nums">
              <span className={up ? "text-emerald-400" : "text-red-400"}>
                {fmtUSDSigned(change.abs)} / {fmtPctSigned(change.pct)}
              </span>
              <span className="text-zinc-600"> · {range}</span>
            </p>
          ) : (
            <p className="mt-1 text-xs text-zinc-600">Account equity over time</p>
          )}
        </div>
        <div className="inline-flex rounded-lg bg-zinc-800/50 p-0.5">
          {RANGES.map((r) => (
            <button
              key={r}
              onClick={() => setRange(r)}
              className={`rounded-md px-2.5 py-1 text-[11px] font-medium transition-colors duration-150 ${
                range === r ? "bg-zinc-100 text-zinc-900" : "text-zinc-400 hover:text-zinc-200"
              }`}
            >
              {r}
            </button>
          ))}
        </div>
      </div>

      {loading ? (
        <div className="flex items-center justify-center h-[260px] gap-2 text-zinc-500 text-xs">
          <span className="inline-block w-4 h-4 border-2 border-zinc-600 border-t-zinc-300 rounded-full animate-spin" />
          Loading equity history…
        </div>
      ) : error ? (
        <p className="text-red-400 text-xs py-12 text-center">{error}</p>
      ) : rows.length < 2 ? (
        <p className="text-zinc-500 text-xs py-12 text-center">Not enough equity history for this range yet.</p>
      ) : (
        <ResponsiveContainer width="100%" height={260}>
          <AreaChart data={rows} margin={{ top: 6, right: 8, bottom: 0, left: -8 }}>
            <defs>
              <linearGradient id="equityFill" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={stroke} stopOpacity={0.28} />
                <stop offset="100%" stopColor={stroke} stopOpacity={0} />
              </linearGradient>
            </defs>
            <XAxis
              dataKey="label"
              tick={{ fill: "#71717a", fontSize: 10 }}
              axisLine={false}
              tickLine={false}
              minTickGap={48}
            />
            <YAxis
              domain={yDomain ?? ["auto", "auto"]}
              tick={{ fill: "#71717a", fontSize: 10 }}
              axisLine={false}
              tickLine={false}
              width={64}
              tickFormatter={(v: number) => fmtUSD(v, { decimals: 0 })}
            />
            <Tooltip
              contentStyle={{ background: "#18181b", border: "1px solid #3f3f46", borderRadius: 6 }}
              labelStyle={{ color: "#e4e4e7", fontSize: 11 }}
              itemStyle={{ fontSize: 11 }}
              formatter={(v: number) => [fmtUSD(v), "Equity"]}
              labelFormatter={(_l, payload) => {
                const ts = payload?.[0]?.payload?.ts as string | undefined;
                return ts ? fmtDate(ts) : "";
              }}
            />
            <Area
              type="monotone"
              dataKey="equity"
              stroke={stroke}
              strokeWidth={1.6}
              fill="url(#equityFill)"
              dot={false}
            />
          </AreaChart>
        </ResponsiveContainer>
      )}
    </div>
  );
}
