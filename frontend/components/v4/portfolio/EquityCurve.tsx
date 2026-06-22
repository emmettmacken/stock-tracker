"use client";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer, ReferenceLine,
} from "recharts";
import { PortfolioHistoryPoint } from "@/lib/types";
import { fetchPortfolioHistory } from "@/lib/api";
import { fmtUSD, fmtUSDSigned, fmtPctSigned } from "@/lib/format";

// Selector labels are passed straight through to the backend, which maps each to an
// Alpaca period + resolution (15-min bars for 1D, hourly for 1W, daily beyond) and
// handles YTD (from Jan 1) and Max (from account creation) server-side.
const RANGES = ["1D", "1W", "1M", "3M", "YTD", "1Y", "Max"] as const;
type Range = (typeof RANGES)[number];

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
function fmtDate(iso: string): string {
  const d = new Date(iso);
  return `${MONTHS[d.getUTCMonth()]} ${d.getUTCDate()}`;
}
// DD/MM HH:MM in the viewer's local time — all parts from the same Date object so the
// day and time can't disagree across the UTC boundary.
function fmtDateTime(iso: string): string {
  const d = new Date(iso);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${p(d.getDate())}/${p(d.getMonth() + 1)} ${p(d.getHours())}:${p(d.getMinutes())}`;
}
// 1D shows intraday DD/MM HH:MM; every other range shows the date (MMM DD).
function fmtAxis(iso: string, range: Range): string {
  return range === "1D" ? fmtDateTime(iso) : fmtDate(iso);
}

interface Row {
  ts: string;
  label: string;
  equity: number;
}

export function EquityCurve() {
  const [range, setRange] = useState<Range>("1D");
  const [points, setPoints] = useState<PortfolioHistoryPoint[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // Index of the data point the cursor is nearest to — drives the vertical crosshair.
  const [activeIndex, setActiveIndex] = useState<number | null>(null);
  // True while the cursor is over the chart. A ref (not state) so toggling it never
  // re-renders — that's the whole point: the live 1D poll must not redraw mid-hover.
  const isHovering = useRef(false);

  // Initial / range-change load — shows the spinner and resets the crosshair.
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    setActiveIndex(null);
    fetchPortfolioHistory(range)
      .then((d) => {
        if (cancelled) return;
        if (!d.available) { setError(d.error ?? "Equity history unavailable"); setPoints(null); }
        else setPoints(d.points ?? []);
      })
      .catch((e) => { if (!cancelled) setError(e instanceof Error ? e.message : "Failed to load"); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [range]);

  // Background refetch (no spinner, keeps last good data on error) for the live poll and
  // the hover-release catch-up. Only the points update, so the chart redraws in place.
  const refetch = useCallback(() => {
    fetchPortfolioHistory(range)
      .then((d) => { if (d.available) setPoints(d.points ?? []); })
      .catch(() => { /* keep the last good curve on a transient background failure */ });
  }, [range]);

  // The 1D view is live intraday — poll every 60s (matching the backend's 1D cache TTL),
  // but skip any tick while the cursor is over the chart so dragging never flickers. Longer
  // ranges are effectively static, so they don't poll. Positions/edge stats poll elsewhere.
  useEffect(() => {
    if (range !== "1D") return;
    const id = setInterval(() => { if (!isHovering.current) refetch(); }, 60_000);
    return () => clearInterval(id);
  }, [range, refetch]);

  const onChartEnter = useCallback(() => { isHovering.current = true; }, []);
  const onChartLeave = useCallback(() => {
    isHovering.current = false;
    setActiveIndex(null);
    // Catch up on any 1D updates skipped while the user was interacting.
    if (range === "1D") refetch();
  }, [range, refetch]);

  const rows = useMemo<Row[]>(() => {
    if (!points) return [];
    return points
      .filter((p) => p.equity != null)
      .map((p) => ({ ts: p.timestamp, label: fmtAxis(p.timestamp, range), equity: p.equity }));
  }, [points, range]);

  // Total return $ and % across the visible window (first → last equity).
  const change = useMemo(() => {
    if (rows.length < 2) return null;
    const first = rows[0].equity;
    const last = rows[rows.length - 1].equity;
    if (!first) return null;
    return { abs: last - first, pct: (last / first - 1) * 100 };
  }, [rows]);

  // Current drawdown from the all-time peak equity within the visible window.
  // Informational (not an alert) — recomputes whenever the period selector changes.
  const drawdown = useMemo(() => {
    if (!rows.length) return null;
    const peak = Math.max(...rows.map((r) => r.equity));
    const current = rows[rows.length - 1].equity;
    if (!peak) return null;
    const pct = ((current - peak) / peak) * 100;
    return { pct, atPeak: current >= peak };
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
          {drawdown != null && (
            <p className={`mt-0.5 text-[11px] tabular-nums ${drawdown.atPeak ? "text-emerald-500/70" : "text-zinc-500"}`}>
              {drawdown.atPeak
                ? "Drawdown from peak: 0.00% (at peak)"
                : `Drawdown from peak: ${drawdown.pct.toFixed(2)}%`}
            </p>
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
        <div onMouseEnter={onChartEnter} onMouseLeave={onChartLeave}>
        <ResponsiveContainer width="100%" height={260}>
          <AreaChart
            data={rows}
            margin={{ top: 6, right: 8, bottom: 0, left: -8 }}
            onMouseMove={(s: { activeTooltipIndex?: number }) =>
              setActiveIndex(typeof s?.activeTooltipIndex === "number" ? s.activeTooltipIndex : null)
            }
            onMouseLeave={() => setActiveIndex(null)}
          >
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
              cursor={false}
              contentStyle={{ background: "#18181b", border: "1px solid #3f3f46", borderRadius: 6 }}
              labelStyle={{ color: "#e4e4e7", fontSize: 11 }}
              itemStyle={{ fontSize: 11 }}
              formatter={(v: number) => [fmtUSD(v), "Equity"]}
              labelFormatter={(_l, payload) => {
                const ts = payload?.[0]?.payload?.ts as string | undefined;
                return ts ? fmtAxis(ts, range) : "";
              }}
            />
            {/* Vertical crosshair that follows the cursor to the nearest point. With the
                denser intraday data (15-min for 1D, hourly for 1W) this tracks fluidly. */}
            {activeIndex != null && rows[activeIndex] && (
              <ReferenceLine
                x={rows[activeIndex].label}
                stroke="#52525b"
                strokeWidth={1}
                strokeDasharray="3 3"
              />
            )}
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
        </div>
      )}
    </div>
  );
}
