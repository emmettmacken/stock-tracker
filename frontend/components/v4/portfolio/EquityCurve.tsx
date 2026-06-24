"use client";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer, ReferenceLine,
} from "recharts";
import { PortfolioHistoryPoint } from "@/lib/types";
import { fetchPortfolioHistory, fetchLiveEquity } from "@/lib/api";
import { fmtUSD, fmtUSDSigned, fmtPctSigned } from "@/lib/format";

// Roughly "is the US market open right now?" — 14:30–21:00 UTC, Mon–Fri. Used only to gate
// the live-tip poll, so an approximation (no holiday calendar) is fine; off-hours we don't
// poll at all. Comparing as integer HHMM avoids minute-by-minute float math.
function isMarketLikelyOpen(now: Date = new Date()): boolean {
  const day = now.getUTCDay();
  if (day === 0 || day === 6) return false;
  const hhmm = now.getUTCHours() * 100 + now.getUTCMinutes();
  return hhmm >= 1430 && hhmm < 2100;
}

// Selector labels are passed straight through to the backend, which serves 1D from our
// own equity snapshots and every other period from Alpaca (1D bars): 1W/1M/3M by period,
// YTD from Jan 1, and 1Y/Max from account creation.
const RANGES = ["1D", "1W", "1M", "3M", "YTD", "1Y", "Max"] as const;
type Range = (typeof RANGES)[number];

// Always show date + time (DD/MM HH:MM) across every period so the axis and tooltip stay
// consistent — 1D bars are intraday and the rest are daily, but a midnight-anchored daily
// bar still reads cleanly as "00:00". All parts come from the same Date object so the day
// and time can't disagree across the UTC boundary.
function fmtLabel(iso: string): string {
  const d = new Date(iso);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${p(d.getDate())}/${p(d.getMonth() + 1)} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

interface Row {
  ts: string;
  label: string;
  equity: number;
}

// Merge a freshly-polled tip into the series tail: replace today's last point in place,
// or append it as the first bar of a new session. Pure so it can run both from the live
// poll and from the deferred flush when the cursor leaves the chart.
function mergeTip(
  prev: PortfolioHistoryPoint[] | null,
  tip: PortfolioHistoryPoint,
): PortfolioHistoryPoint[] | null {
  if (!prev || prev.length === 0) return prev;
  const last = prev[prev.length - 1];
  const sameDay = last.timestamp.slice(0, 10) === tip.timestamp.slice(0, 10);
  return sameDay ? [...prev.slice(0, -1), tip] : [...prev, tip];
}

interface EquityCurveProps {
  // Called on every successful live-tip fetch (10s, market hours) with the latest equity —
  // fires even while the user is hovering, so the page's stat cards stay current.
  onLiveEquity?: (equity: number) => void;
  // Frontend-only preference: draw a dotted horizontal line at the starting balance.
  showNetDeposits?: boolean;
  netDepositsLevel?: number;
}

export function EquityCurve({ onLiveEquity, showNetDeposits = false, netDepositsLevel = 100_000 }: EquityCurveProps = {}) {
  const [range, setRange] = useState<Range>("1D");
  const [points, setPoints] = useState<PortfolioHistoryPoint[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // Index of the data point the cursor is nearest to — drives the vertical crosshair.
  const [activeIndex, setActiveIndex] = useState<number | null>(null);
  // While the cursor is over the chart we defer the tip's setPoints (which would re-render
  // and flicker the crosshair) — the latest deferred tip is parked here and flushed on leave.
  const isHovering = useRef(false);
  const pendingTip = useRef<PortfolioHistoryPoint | null>(null);
  // Keep onLiveEquity in a ref so the polling effect's interval doesn't tear down/recreate
  // when the parent passes a fresh callback identity.
  const onLiveEquityRef = useRef(onLiveEquity);
  onLiveEquityRef.current = onLiveEquity;

  // Initial / range-change load — the only time the full dataset is fetched. Shows the
  // spinner and resets the crosshair. Live updates (1D) extend just the tip below.
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

  // Live tip (1D only): poll just the current equity value every 10s during market hours and
  // mutate only the last point — replacing today's tip in place, or appending the first bar
  // of a new session. The functional update touches just the tail (Area animation is off, so
  // the new point appears without replaying the draw). Longer ranges are historical and
  // never poll. The fetch always runs and always reports the value upward; only the setPoints
  // (which would flicker the crosshair) is deferred while the cursor is over the chart.
  useEffect(() => {
    if (range !== "1D") return;
    const tick = () => {
      if (!isMarketLikelyOpen()) return;
      fetchLiveEquity()
        .then((d) => {
          if (typeof d?.equity !== "number") return;
          const tip = { timestamp: d.timestamp, equity: d.equity };
          onLiveEquityRef.current?.(d.equity);
          if (isHovering.current) {
            // Defer the visual update — park the latest tip; it's flushed on mouse leave.
            pendingTip.current = tip;
          } else {
            setPoints((prev) => mergeTip(prev, tip));
          }
        })
        .catch(() => { /* transient — keep the last good tip, try again next tick */ });
    };
    const id = setInterval(tick, 10_000);
    return () => clearInterval(id);
  }, [range]);

  const rows = useMemo<Row[]>(() => {
    if (!points) return [];
    return points
      .filter((p) => p.equity != null)
      .map((p) => ({ ts: p.timestamp, label: fmtLabel(p.timestamp), equity: p.equity }));
  }, [points]);

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
    // Expand the domain to keep the net-deposits reference line in view when enabled.
    if (showNetDeposits) vals.push(netDepositsLevel);
    const lo = Math.min(...vals), hi = Math.max(...vals);
    const pad = (hi - lo) * 0.08 || hi * 0.02;
    return [lo - pad, hi + pad];
  }, [rows, showNetDeposits, netDepositsLevel]);

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
        <div
          onMouseEnter={() => { isHovering.current = true; }}
          onMouseLeave={() => {
            isHovering.current = false;
            // Catch up instantly if a tip arrived while hovering, then drop it.
            if (pendingTip.current) {
              const tip = pendingTip.current;
              pendingTip.current = null;
              setPoints((prev) => mergeTip(prev, tip));
            }
          }}
        >
        <ResponsiveContainer width="100%" height={260}>
          <AreaChart
            data={rows}
            margin={{ top: 6, right: 8, bottom: 0, left: -8 }}
            onMouseMove={(s: { activeTooltipIndex?: number }) => {
              // Only commit a state change when the nearest bar actually changes — Recharts
              // fires onMouseMove on every pixel, and re-rendering within the same bar was
              // replaying the Area draw animation (chart flicker on enter).
              const next = typeof s?.activeTooltipIndex === "number" ? s.activeTooltipIndex : null;
              setActiveIndex((prev) => (prev === next ? prev : next));
            }}
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
                return ts ? fmtLabel(ts) : "";
              }}
            />
            {/* Net-deposits reference line at the starting balance (opt-in setting). */}
            {showNetDeposits && (
              <ReferenceLine
                y={netDepositsLevel}
                stroke="#a1a1aa"
                strokeWidth={1}
                strokeDasharray="4 4"
                label={{
                  value: `Net deposits ${fmtUSD(netDepositsLevel, { decimals: 0 })}`,
                  position: "insideTopLeft",
                  fill: "#a1a1aa",
                  fontSize: 10,
                }}
              />
            )}
            {/* Vertical crosshair that follows the cursor to the nearest point. Tracks
                fluidly on dense 1D snapshot data and step-wise on daily bars alike. */}
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
              isAnimationActive={false}
            />
          </AreaChart>
        </ResponsiveContainer>
        </div>
      )}
    </div>
  );
}
