"use client";
import { useState, useEffect, useCallback } from "react";
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ReferenceLine,
  ResponsiveContainer, Cell,
} from "recharts";
import { AnalyticsData, AnalyticsTickerPerf } from "@/lib/types";
import { fetchAnalytics } from "@/lib/api";
import { Skeleton } from "@/components/v3/Skeleton";

// ── Formatting helpers ────────────────────────────────────────────────────────

function fmtTs(ts: string | null): string {
  if (!ts) return "—";
  try {
    return new Date(ts + "Z").toLocaleString();
  } catch {
    return ts.slice(0, 16).replace("T", " ");
  }
}

function fmtPct(v: number, signed = false): string {
  const s = v >= 0 && signed ? "+" : "";
  return `${s}${v.toFixed(2)}%`;
}

// ── Sub-components ────────────────────────────────────────────────────────────

function ChartSkeleton() {
  return (
    <div className="space-y-2">
      <Skeleton className="h-4 w-1/3" />
      <Skeleton className="h-36 w-full" />
    </div>
  );
}

function SectionTitle({ children }: { children: React.ReactNode }) {
  return <h3 className="text-xs font-semibold text-zinc-400 mb-3 uppercase tracking-wide">{children}</h3>;
}

type SortKey = "win_rate" | "avg_return" | "total_trades";
type SortDir = "asc" | "desc";

function TickerTable({ rows }: { rows: AnalyticsTickerPerf[] }) {
  const [sortKey, setSortKey] = useState<SortKey>("win_rate");
  const [sortDir, setSortDir] = useState<SortDir>("desc");

  function handleSort(key: SortKey) {
    if (key === sortKey) {
      setSortDir((d) => (d === "desc" ? "asc" : "desc"));
    } else {
      setSortKey(key);
      setSortDir("desc");
    }
  }

  const sorted = [...rows].sort((a, b) => {
    const diff = a[sortKey] - b[sortKey];
    return sortDir === "desc" ? -diff : diff;
  });

  const cols: { label: string; key: SortKey }[] = [
    { label: "Win Rate", key: "win_rate" },
    { label: "Avg Return", key: "avg_return" },
    { label: "Trades", key: "total_trades" },
  ];

  function rowColor(wr: number): string {
    if (wr > 0.6) return "text-emerald-400";
    if (wr < 0.4) return "text-red-400";
    return "text-zinc-300";
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead>
          <tr className="border-b border-zinc-800 text-left">
            <th className="py-2 pr-4 text-zinc-500 font-medium">Ticker</th>
            {cols.map((c) => (
              <th
                key={c.key}
                className="py-2 pr-4 text-zinc-500 font-medium cursor-pointer hover:text-zinc-300 select-none whitespace-nowrap"
                onClick={() => handleSort(c.key)}
              >
                {c.label} {sortKey === c.key ? (sortDir === "desc" ? "↓" : "↑") : ""}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {sorted.map((r) => (
            <tr key={r.ticker} className="border-b border-zinc-800/50 hover:bg-zinc-800/30">
              <td className="py-2.5 pr-4 font-bold text-zinc-200">{r.ticker}</td>
              <td className={`py-2.5 pr-4 tabular-nums font-semibold ${rowColor(r.win_rate)}`}>
                {(r.win_rate * 100).toFixed(0)}%
              </td>
              <td className={`py-2.5 pr-4 tabular-nums font-semibold ${r.avg_return >= 0 ? "text-emerald-400" : "text-red-400"}`}>
                {fmtPct(r.avg_return, true)}
              </td>
              <td className="py-2.5 pr-4 tabular-nums text-zinc-400">{r.total_trades}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function HealthChip({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="bg-zinc-800/60 rounded-lg px-3 py-2.5">
      <div className="text-[10px] text-zinc-500 mb-0.5">{label}</div>
      <div className="text-xs font-semibold text-zinc-200">{value}</div>
    </div>
  );
}

const EXIT_LABELS: Record<string, string> = {
  stop_loss:               "Stop Loss",
  sell_signal:             "Sell Signal",
  max_hold_exit:           "Time Exit",
  score_deterioration:     "Score Drop",
  macro_drawdown_protection: "Macro Exit",
};

// ── Main component ────────────────────────────────────────────────────────────

export function AnalyticsTab() {
  const [data, setData] = useState<AnalyticsData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    fetchAnalytics()
      .then(setData)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => { load(); }, [load]);

  return (
    <div className="space-y-8">
      {/* Header + refresh */}
      <div className="flex items-center justify-between">
        <p className="text-xs text-zinc-500">Signal calibration and system health data</p>
        <button
          onClick={load}
          disabled={loading}
          className="px-3 py-1.5 text-xs bg-zinc-800 hover:bg-zinc-700 disabled:opacity-50 rounded-lg transition-colors"
        >
          {loading ? "Loading…" : "Refresh"}
        </button>
      </div>

      {error && (
        <div className="text-red-400 text-xs">
          {error}{" "}
          <button onClick={load} className="underline text-zinc-400 hover:text-white ml-1">Retry</button>
        </div>
      )}

      {/* Score calibration chart */}
      <div>
        <SectionTitle>Score Calibration — Win Rate by Entry Score</SectionTitle>
        {loading ? <ChartSkeleton /> : data && data.by_score_bucket.length > 0 ? (
          <ResponsiveContainer width="100%" height={160}>
            <BarChart data={data.by_score_bucket} margin={{ top: 4, right: 8, left: -16, bottom: 0 }}>
              <XAxis dataKey="bucket" tick={{ fontSize: 10, fill: "#71717a" }} />
              <YAxis
                tickFormatter={(v) => `${(v * 100).toFixed(0)}%`}
                domain={[0, 1]}
                tick={{ fontSize: 10, fill: "#71717a" }}
              />
              <Tooltip
                contentStyle={{ background: "#18181b", border: "1px solid #3f3f46", borderRadius: 6 }}
                labelStyle={{ color: "#a1a1aa", fontSize: 11 }}
                formatter={(v: number, name: string) =>
                  name === "win_rate"
                    ? [`${(v * 100).toFixed(0)}%`, "Win Rate"]
                    : [v, name]
                }
              />
              <ReferenceLine y={0.5} stroke="#52525b" strokeDasharray="4 2" label={{ value: "50%", position: "right", fontSize: 9, fill: "#71717a" }} />
              <Bar dataKey="win_rate" radius={[3, 3, 0, 0]}>
                {data.by_score_bucket.map((entry) => (
                  <Cell
                    key={entry.bucket}
                    fill={entry.win_rate >= 0.5 ? "#10b981" : "#f87171"}
                  />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        ) : !loading && (
          <p className="text-zinc-600 text-xs py-6 text-center">No closed trades with score data yet.</p>
        )}
        {!loading && data && data.by_score_bucket.length > 0 && (
          <p className="text-[10px] text-zinc-600 mt-1">
            Bars rising left → right confirm scores are predictive. Reference line = 50% baseline.
          </p>
        )}
      </div>

      {/* Exit reason chart */}
      <div>
        <SectionTitle>Exit Reason Breakdown — Avg Return per Exit Type</SectionTitle>
        {loading ? <ChartSkeleton /> : data && data.by_exit_reason.length > 0 ? (
          <ResponsiveContainer width="100%" height={160}>
            <BarChart
              data={data.by_exit_reason.map((r) => ({
                ...r,
                label: EXIT_LABELS[r.exit_reason] ?? r.exit_reason.replace(/_/g, " "),
              }))}
              margin={{ top: 4, right: 8, left: -8, bottom: 0 }}
            >
              <XAxis dataKey="label" tick={{ fontSize: 9, fill: "#71717a" }} />
              <YAxis
                tickFormatter={(v) => `${v >= 0 ? "+" : ""}${v.toFixed(1)}%`}
                tick={{ fontSize: 10, fill: "#71717a" }}
              />
              <Tooltip
                contentStyle={{ background: "#18181b", border: "1px solid #3f3f46", borderRadius: 6 }}
                labelStyle={{ color: "#a1a1aa", fontSize: 11 }}
                formatter={(v: number) => [`${v >= 0 ? "+" : ""}${v.toFixed(2)}%`, "Avg Return"]}
              />
              <ReferenceLine y={0} stroke="#52525b" />
              <Bar dataKey="avg_return" radius={[3, 3, 0, 0]}>
                {data.by_exit_reason.map((entry) => (
                  <Cell
                    key={entry.exit_reason}
                    fill={entry.avg_return >= 0 ? "#10b981" : "#f87171"}
                  />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        ) : !loading && (
          <p className="text-zinc-600 text-xs py-6 text-center">No closed trades yet.</p>
        )}
      </div>

      {/* Ticker performance table */}
      <div>
        <SectionTitle>Ticker Performance</SectionTitle>
        <p className="text-[10px] text-zinc-600 mb-3">
          Green = win rate &gt; 60% · Red = win rate &lt; 40% · Click headers to sort
        </p>
        {loading ? (
          <div className="space-y-2">{[1, 2, 3].map((i) => <Skeleton key={i} className="h-8 w-full" />)}</div>
        ) : data && data.by_ticker.length > 0 ? (
          <TickerTable rows={data.by_ticker} />
        ) : (
          <p className="text-zinc-600 text-xs py-4 text-center">No ticker performance data yet.</p>
        )}
      </div>

      {/* Adaptive thresholds */}
      <div>
        <SectionTitle>Adaptive Thresholds</SectionTitle>
        {loading ? (
          <div className="grid grid-cols-3 gap-3">
            {[1, 2, 3].map((i) => <Skeleton key={i} className="h-14 w-full" />)}
          </div>
        ) : data ? (
          <>
            <div className="grid grid-cols-3 gap-3 mb-2">
              <HealthChip label="Bull Threshold" value={`${data.adaptive_thresholds.bull}`} />
              <HealthChip label="Bear Threshold" value={`${data.adaptive_thresholds.bear}`} />
              <HealthChip label="Last Updated" value={
                data.adaptive_thresholds.last_updated
                  ? fmtTs(data.adaptive_thresholds.last_updated)
                  : "Never (using defaults)"
              } />
            </div>
            <p className="text-[10px] text-zinc-600">
              Adjusted weekly: −5 when recent win rate &gt; 60%, +5 when &lt; 40%.
              Range: bull 70–85 · bear 80–90.
            </p>
          </>
        ) : null}
      </div>

      {/* System health */}
      <div>
        <SectionTitle>System Health</SectionTitle>
        {loading ? (
          <div className="grid grid-cols-2 gap-3">{[1, 2, 3, 4].map((i) => <Skeleton key={i} className="h-14 w-full" />)}</div>
        ) : data ? (
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <HealthChip label="Last Signal Job" value={fmtTs(data.system_health.last_signal_job)} />
            <HealthChip label="Last Stop-Loss Job" value={fmtTs(data.system_health.last_stoploss_job)} />
            <HealthChip label="Open Positions" value={data.system_health.open_positions} />
            <HealthChip label="Total Closed Trades" value={data.system_health.total_closed_trades} />
          </div>
        ) : null}
      </div>
    </div>
  );
}
