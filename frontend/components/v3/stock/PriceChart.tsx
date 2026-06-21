"use client";
import { useEffect, useMemo, useState } from "react";
import {
  LineChart, Line, XAxis, YAxis, Tooltip,
  ResponsiveContainer, ReferenceDot, Legend,
} from "recharts";
import { PricePoint, TradeOutcome } from "@/lib/types";
import { fetchPriceHistory, fetchTradeHistory } from "@/lib/api";
import { PERIODS, Period, periodCutoff } from "@/lib/period";

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
  const [trades, setTrades] = useState<TradeOutcome[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showMA, setShowMA] = useState({ ma20: false, ma50: false, ma200: false });

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchPriceHistory(ticker, 760)
      .then((d) => { if (!cancelled) setPoints(d.points); })
      .catch((e) => { if (!cancelled) setError(e instanceof Error ? e.message : "Failed to load prices"); })
      .finally(() => { if (!cancelled) setLoading(false); });
    fetchTradeHistory()
      .then((all) => { if (!cancelled) setTrades(all.filter((t) => t.ticker === ticker)); })
      .catch(() => { /* markers are optional */ });
    return () => { cancelled = true; };
  }, [ticker]);

  // Full-series rows with MAs computed over all available history.
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

  // Slice to the selected period (MAs stay correct since they were computed on full history).
  const rows = useMemo(() => {
    if (!fullRows.length) return [];
    const cutoffStr = periodCutoff(period, fullRows[fullRows.length - 1].date);
    if (!cutoffStr) return fullRows;
    return fullRows.filter((r) => r.date >= cutoffStr);
  }, [fullRows, period]);

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
    if (!rows.length) return [] as { date: string; price: number; kind: "entry" | "exit" }[];
    const dates = rows.map((r) => r.date);
    const first = dates[0];
    const lastD = dates[dates.length - 1];
    const snap = (iso: string | null): string | null => {
      if (!iso) return null;
      const d = iso.slice(0, 10);
      if (d < first || d > lastD) return null;
      // nearest existing date <= d, else the first >= d
      let best: string | null = null;
      for (const dd of dates) {
        if (dd <= d) best = dd;
        else { if (best === null) best = dd; break; }
      }
      return best;
    };
    const out: { date: string; price: number; kind: "entry" | "exit" }[] = [];
    for (const t of trades) {
      const e = snap(t.entry_timestamp);
      if (e) out.push({ date: e, price: t.entry_price, kind: "entry" });
      const x = snap(t.exit_timestamp);
      if (x) out.push({ date: x, price: t.exit_price, kind: "exit" });
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
        <div className="flex items-center gap-1">
          {toggleBtn("ma20", "MA20", "#38bdf8")}
          {toggleBtn("ma50", "MA50", "#a78bfa")}
          {toggleBtn("ma200", "MA200", "#f59e0b")}
        </div>
      </div>

      <ResponsiveContainer width="100%" height={260}>
        <LineChart data={rows} margin={{ top: 6, right: 8, bottom: 0, left: -8 }}>
          <XAxis
            dataKey="date"
            tick={{ fill: "#71717a", fontSize: 10 }}
            axisLine={false}
            tickLine={false}
            minTickGap={48}
            tickFormatter={(v: string) => v.slice(5)}
          />
          <YAxis
            domain={yDomain ?? ["auto", "auto"]}
            tick={{ fill: "#71717a", fontSize: 10 }}
            axisLine={false}
            tickLine={false}
            width={52}
            tickFormatter={(v: number) => `€${v.toFixed(0)}`}
          />
          <Tooltip
            contentStyle={{ background: "#18181b", border: "1px solid #3f3f46", borderRadius: 6 }}
            labelStyle={{ color: "#e4e4e7", fontSize: 11 }}
            itemStyle={{ fontSize: 11 }}
            formatter={(v: number, name: string) => [`€${v.toFixed(2)}`, name]}
          />
          <Legend wrapperStyle={{ fontSize: 11, paddingTop: 4 }} iconType="line" />
          <Line type="monotone" dataKey="close" name="Close" stroke="#10b981" strokeWidth={1.6} dot={false} />
          {showMA.ma20 && <Line type="monotone" dataKey="ma20" name="MA20" stroke="#38bdf8" strokeWidth={1.2} dot={false} connectNulls />}
          {showMA.ma50 && <Line type="monotone" dataKey="ma50" name="MA50" stroke="#a78bfa" strokeWidth={1.2} dot={false} connectNulls />}
          {showMA.ma200 && <Line type="monotone" dataKey="ma200" name="MA200" stroke="#f59e0b" strokeWidth={1.2} dot={false} connectNulls />}
          {markers.map((m, i) => (
            <ReferenceDot
              key={`${m.kind}-${m.date}-${i}`}
              x={m.date}
              y={m.price}
              r={4}
              fill={m.kind === "entry" ? "#10b981" : "#ef4444"}
              stroke="#09090b"
              strokeWidth={1.5}
              isFront
            />
          ))}
        </LineChart>
      </ResponsiveContainer>

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
