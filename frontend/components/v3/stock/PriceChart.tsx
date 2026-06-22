"use client";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  LineChart, Line, XAxis, YAxis, Tooltip,
  ResponsiveContainer, ReferenceDot, ReferenceLine, Legend,
} from "recharts";
import { PricePoint, TradeOutcome } from "@/lib/types";
import { fetchPriceHistory, fetchTradeHistory } from "@/lib/api";
import { PERIODS, Period } from "@/lib/period";

// 1D/1W are served as intraday bars (1m / 15m) whose `date` is a full ISO timestamp;
// they render as DD/MM HH:MM. Every longer range is daily and renders as MMM DD.
function isIntraday(period: Period): boolean {
  return period === "1D" || period === "1W";
}

// Simple moving average over the full close series (display-only; not used in scoring).
function movingAverage(closes: number[], window: number): (number | null)[] {
  const out: (number | null)[] = new Array(closes.length).fill(null);
  let sum = 0;
  for (let i = 0; i < closes.length; i++) {
    sum += closes[i];
    if (i >= window) sum -= closes[i - window];
    if (i >= window - 1) out[i] = sum / window;
  }
  return out;
}

interface Row {
  date: string;
  close: number;
  ma20: number | null;
  ma50: number | null;
  ma200: number | null;
}

// A plotted entry/exit dot. `price` is the actual fill price (Alpaca) — what we plot on Y;
// `close` is the same date's daily close (yfinance) from the price line, for the gap explainer.
interface Marker {
  date: string;
  price: number;
  close: number | null;
  gapPct: number | null;
  kind: "entry" | "exit";
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
// Format "2026-06-18" → "Jun 18" without going through Date() (avoids UTC→local off-by-one).
function fmtShortDate(iso: string): string {
  const [, mo, d] = iso.split("-").map(Number);
  return `${MONTHS[mo - 1]} ${Number(d)}`;
}
// Intraday ISO timestamp → "DD/MM HH:MM" in local time (all parts from one Date object).
function fmtDateTime(iso: string): string {
  const dt = new Date(iso);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${p(dt.getDate())}/${p(dt.getMonth() + 1)} ${p(dt.getHours())}:${p(dt.getMinutes())}`;
}
// Axis/tooltip label for a data point: DD/MM HH:MM for intraday ranges, MMM DD otherwise.
function fmtPointLabel(date: string, intraday: boolean): string {
  return intraday ? fmtDateTime(date) : fmtShortDate(date);
}

export function PriceChart({
  ticker,
  period,
  onPeriodChange,
}: {
  ticker: string;
  period: Period;
  onPeriodChange: (p: Period) => void;
}) {
  const [points, setPoints] = useState<PricePoint[] | null>(null);
  // ISO date the chart should start drawing from (daily periods only). Points before it are
  // MA lead-in: used to compute MA50/MA200 but trimmed off the axis. null = draw everything.
  const [visibleFrom, setVisibleFrom] = useState<string | null>(null);
  const [trades, setTrades] = useState<TradeOutcome[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showMA, setShowMA] = useState({ ma20: false, ma50: false, ma200: false });
  // Marker the user is hovering/tapping, with its pixel coords from Recharts, for the explainer tooltip.
  const [activeMarker, setActiveMarker] = useState<{ m: Marker; cx: number; cy: number } | null>(null);
  // Index of the data point nearest the cursor — drives the vertical crosshair.
  const [activeIndex, setActiveIndex] = useState<number | null>(null);
  // True while the cursor is over the chart. A ref (not state) so it never re-renders;
  // there's no background poll here today, but this keeps the pattern ready so any future
  // refetch can pause during interaction (as the equity curve does).
  const isHovering = useRef(false);

  const intraday = isIntraday(period);

  // The backend scopes the window + resolution to the selected period (intraday 1m/15m for
  // 1D/1W, daily for 1M+, full history for Max), so we refetch on every period change.
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    setActiveIndex(null);
    fetchPriceHistory(ticker, { period })
      .then((d) => { if (!cancelled) { setPoints(d.points); setVisibleFrom(d.visible_from ?? null); } })
      .catch((e) => { if (!cancelled) setError(e instanceof Error ? e.message : "Failed to load prices"); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [ticker, period]);

  // Trades (for entry/exit markers) don't depend on the period window, so fetch them once per ticker.
  useEffect(() => {
    let cancelled = false;
    fetchTradeHistory()
      .then((all) => { if (!cancelled) setTrades(all.filter((t) => t.ticker === ticker)); })
      .catch(() => { /* markers are optional */ });
    return () => { cancelled = true; };
  }, [ticker]);

  // Rows with MAs computed over the *full* returned series (which includes the daily periods'
  // MA lead-in). MA windows are bar counts: on intraday ranges they're short-horizon
  // (e.g. MA20 = 20 minutes on 1D). The backend bounds the visible window, so MAs are valid
  // from the first visible bar.
  const fullRows: Row[] = useMemo(() => {
    if (!points) return [];
    const closes = points.map((p) => p.close);
    const ma20 = movingAverage(closes, 20);
    const ma50 = movingAverage(closes, 50);
    const ma200 = movingAverage(closes, 200);
    return points.map((p, i) => ({
      date: p.date, close: p.close,
      ma20: ma20[i], ma50: ma50[i], ma200: ma200[i],
    }));
  }, [points]);

  // Trim the MA lead-in off the axis: daily periods send `visible_from`; everything else
  // (intraday, Max) draws the full series. MAs stay correct since they're computed above.
  const rows: Row[] = useMemo(
    () => (visibleFrom ? fullRows.filter((r) => r.date >= visibleFrom) : fullRows),
    [fullRows, visibleFrom],
  );

  const onChartEnter = useCallback(() => { isHovering.current = true; }, []);
  const onChartLeave = useCallback(() => { isHovering.current = false; setActiveIndex(null); }, []);

  // +/- price change across the visible window, shown next to the period selector.
  const periodChange = useMemo<number | null>(() => {
    if (rows.length < 2) return null;
    const first = rows[0].close;
    const last = rows[rows.length - 1].close;
    if (!first) return null;
    return (last / first - 1) * 100;
  }, [rows]);

  // Snap a trade date to the nearest visible trading day so the marker renders on-axis.
  const markers = useMemo(() => {
    if (!rows.length) return [] as Marker[];
    const dates = rows.map((r) => r.date);
    // Reuse the already-loaded price-history closes (no extra fetch) to explain the marker-vs-line gap.
    const closeByDate = new Map(rows.map((r) => [r.date, r.close]));
    // Compare on the date prefix so intraday timestamps ("...T09:30:00-04:00") snap by day too.
    const first = dates[0].slice(0, 10);
    const lastD = dates[dates.length - 1].slice(0, 10);
    const snap = (iso: string | null): string | null => {
      if (!iso) return null;
      const d = iso.slice(0, 10);
      if (d < first || d > lastD) return null;
      // nearest existing bar on/before d, else the first bar after it. `best` is the full
      // `date` value (timestamp on intraday ranges) so the ReferenceDot lands on a real point.
      let best: string | null = null;
      for (const dd of dates) {
        if (dd.slice(0, 10) <= d) best = dd;
        else { if (best === null) best = dd; break; }
      }
      return best;
    };
    const build = (date: string, price: number, kind: "entry" | "exit"): Marker => {
      const close = closeByDate.get(date) ?? null;
      const gapPct = close ? (price / close - 1) * 100 : null;
      return { date, price, close, gapPct, kind };
    };
    const out: Marker[] = [];
    for (const t of trades) {
      const e = snap(t.entry_timestamp);
      if (e) out.push(build(e, t.entry_price, "entry"));
      const x = snap(t.exit_timestamp);
      if (x) out.push(build(x, t.exit_price, "exit"));
    }
    return out;
  }, [rows, trades]);

  const yDomain = useMemo<[number, number] | undefined>(() => {
    if (!rows.length) return undefined;
    const vals = rows.map((r) => r.close);
    const lo = Math.min(...vals), hi = Math.max(...vals);
    const pad = (hi - lo) * 0.08 || hi * 0.05;
    return [lo - pad, hi + pad];
  }, [rows]);

  const toggleBtn = (key: keyof typeof showMA, label: string, color: string) => (
    <button
      onClick={() => setShowMA((s) => ({ ...s, [key]: !s[key] }))}
      className={`inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-[11px] font-medium transition-colors duration-150 ${
        showMA[key] ? "bg-zinc-800 text-zinc-100" : "text-zinc-500 hover:text-zinc-300"
      }`}
    >
      <span className="inline-block h-0.5 w-3 rounded" style={{ background: color }} />
      {label}
    </button>
  );

  if (loading) {
    return (
      <div className="flex items-center justify-center py-12 gap-2 text-zinc-500 text-xs">
        <span className="inline-block w-4 h-4 border-2 border-zinc-600 border-t-zinc-300 rounded-full animate-spin" />
        Loading price history…
      </div>
    );
  }
  if (error) return <p className="text-red-400 text-xs py-4 text-center">{error}</p>;
  if (!rows.length) return <p className="text-zinc-500 text-xs py-4 text-center">No price history available.</p>;

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div className="flex items-center gap-3">
          <div className="inline-flex rounded-lg bg-zinc-800/50 p-0.5">
            {PERIODS.map((key) => (
              <button
                key={key}
                onClick={() => onPeriodChange(key)}
                className={`rounded-md px-2.5 py-1 text-[11px] font-medium transition-colors duration-150 ${
                  period === key ? "bg-zinc-100 text-zinc-900" : "text-zinc-400 hover:text-zinc-200"
                }`}
              >
                {key}
              </button>
            ))}
          </div>
          {periodChange != null && (
            <span className="text-xs font-medium tabular-nums">
              <span className="text-zinc-500">{period}:</span>{" "}
              <span className={periodChange >= 0 ? "text-emerald-400" : "text-red-400"}>
                {periodChange >= 0 ? "+" : ""}{periodChange.toFixed(1)}%
              </span>
            </span>
          )}
        </div>
        {/* MAs are meaningless at intraday resolution (MA20 = 20 minutes on 1D), so hide the
            toggles on 1D/1W. State is preserved — only the UI is hidden — so switching back to a
            daily period restores whatever the user had on. */}
        {!intraday && (
          <div className="flex items-center gap-1">
            {toggleBtn("ma20", "MA20", "#38bdf8")}
            {toggleBtn("ma50", "MA50", "#a78bfa")}
            {toggleBtn("ma200", "MA200", "#f59e0b")}
          </div>
        )}
      </div>

      <div className="relative" onMouseEnter={onChartEnter} onMouseLeave={onChartLeave}>
      <ResponsiveContainer width="100%" height={260}>
        <LineChart
          data={rows}
          margin={{ top: 6, right: 8, bottom: 0, left: -8 }}
          onMouseMove={(s: { activeTooltipIndex?: number }) => {
            // Only commit when the nearest bar changes — Recharts fires this on every pixel,
            // and re-rendering within the same bar replayed the Line draw animation (flicker).
            const next = typeof s?.activeTooltipIndex === "number" ? s.activeTooltipIndex : null;
            setActiveIndex((prev) => (prev === next ? prev : next));
          }}
          onMouseLeave={() => setActiveIndex(null)}
        >
          <XAxis
            dataKey="date"
            tick={{ fill: "#71717a", fontSize: 10 }}
            axisLine={false}
            tickLine={false}
            minTickGap={48}
            tickFormatter={(v: string) => fmtPointLabel(v, intraday)}
          />
          <YAxis
            domain={yDomain ?? ["auto", "auto"]}
            tick={{ fill: "#71717a", fontSize: 10 }}
            axisLine={false}
            tickLine={false}
            width={52}
            tickFormatter={(v: number) => `$${v.toFixed(0)}`}
          />
          <Tooltip
            cursor={false}
            contentStyle={{ background: "#18181b", border: "1px solid #3f3f46", borderRadius: 6 }}
            labelStyle={{ color: "#e4e4e7", fontSize: 11 }}
            itemStyle={{ fontSize: 11 }}
            formatter={(v: number, name: string) => [`$${v.toFixed(2)}`, name]}
            labelFormatter={(label: string) => fmtPointLabel(label, intraday)}
          />
          {/* Vertical crosshair following the cursor to the nearest bar (Recharts' own
              cursor is disabled above so this is the single, smooth indicator). */}
          {activeIndex != null && rows[activeIndex] && (
            <ReferenceLine
              x={rows[activeIndex].date}
              stroke="#52525b"
              strokeWidth={1}
              strokeDasharray="3 3"
            />
          )}
          <Legend wrapperStyle={{ fontSize: 11, paddingTop: 4 }} iconType="line" />
          <Line type="monotone" dataKey="close" name="Close" stroke="#10b981" strokeWidth={1.6} dot={false} isAnimationActive={false} />
          {!intraday && showMA.ma20 && <Line type="monotone" dataKey="ma20" name="MA20" stroke="#38bdf8" strokeWidth={1.2} dot={false} connectNulls isAnimationActive={false} />}
          {!intraday && showMA.ma50 && <Line type="monotone" dataKey="ma50" name="MA50" stroke="#a78bfa" strokeWidth={1.2} dot={false} connectNulls isAnimationActive={false} />}
          {!intraday && showMA.ma200 && <Line type="monotone" dataKey="ma200" name="MA200" stroke="#f59e0b" strokeWidth={1.2} dot={false} connectNulls isAnimationActive={false} />}
          {markers.map((m, i) => (
            <ReferenceDot
              key={`${m.kind}-${m.date}-${i}`}
              x={m.date}
              y={m.price}
              isFront
              shape={(props: { cx?: number; cy?: number }) => {
                const cx = props.cx ?? 0;
                const cy = props.cy ?? 0;
                const fill = m.kind === "entry" ? "#10b981" : "#ef4444";
                const show = () => setActiveMarker({ m, cx, cy });
                return (
                  <g>
                    {/* Visible dot — unchanged size/position. */}
                    <circle cx={cx} cy={cy} r={4} fill={fill} stroke="#09090b" strokeWidth={1.5} />
                    {/* Larger transparent hit area so the tooltip is easy to hover/tap. */}
                    <circle
                      cx={cx}
                      cy={cy}
                      r={11}
                      fill="transparent"
                      style={{ cursor: "pointer" }}
                      onMouseEnter={show}
                      onMouseLeave={() => setActiveMarker(null)}
                      onClick={() =>
                        setActiveMarker((cur) => (cur?.m === m ? null : { m, cx, cy }))
                      }
                    />
                  </g>
                );
              }}
            />
          ))}
        </LineChart>
      </ResponsiveContainer>

      {activeMarker && (
        <div
          className="pointer-events-none absolute z-10 -translate-x-1/2 -translate-y-full rounded-md border border-zinc-700 bg-zinc-900 px-2.5 py-1.5 text-[11px] leading-tight shadow-lg"
          style={{ left: activeMarker.cx, top: activeMarker.cy - 10 }}
        >
          <div className="font-semibold text-zinc-100">
            {activeMarker.m.kind === "entry" ? "Entry" : "Exit"}
            <span className="text-zinc-500"> · {fmtPointLabel(activeMarker.m.date, intraday)}</span>
          </div>
          <div className="mt-0.5 whitespace-nowrap text-zinc-300 tabular-nums">
            Filled ${activeMarker.m.price.toFixed(2)}
            {activeMarker.m.close != null && (
              <span className="text-zinc-500"> · Day close ${activeMarker.m.close.toFixed(2)}</span>
            )}
          </div>
          {activeMarker.m.gapPct != null && (
            <div
              className={`mt-0.5 font-semibold tabular-nums ${
                activeMarker.m.gapPct >= 0 ? "text-emerald-400" : "text-red-400"
              }`}
            >
              {activeMarker.m.gapPct >= 0 ? "+" : "−"}
              {Math.abs(activeMarker.m.gapPct).toFixed(1)}% vs. close
            </div>
          )}
        </div>
      )}
      </div>

      {markers.length > 0 && (
        <div className="flex items-center gap-4 text-[10px] text-zinc-500">
          <span className="inline-flex items-center gap-1.5">
            <span className="inline-block h-2 w-2 rounded-full bg-emerald-500" /> Entry
          </span>
          <span className="inline-flex items-center gap-1.5">
            <span className="inline-block h-2 w-2 rounded-full bg-red-500" /> Exit
          </span>
        </div>
      )}
    </div>
  );
}
