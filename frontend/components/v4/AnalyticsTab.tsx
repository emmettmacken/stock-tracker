"use client";
import { useState, useEffect, useCallback } from "react";
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ReferenceLine, Legend,
  ResponsiveContainer, Cell,
} from "recharts";
import {
  AnalyticsData, AnalyticsTickerPerf,
  FactorContributionData, GateRejectionsData, DrawdownData,
} from "@/lib/types";
import {
  fetchAnalytics, fetchFactorContribution, fetchGateRejections, fetchDrawdown,
} from "@/lib/api";
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
  return <h3 className="text-xs font-semibold text-zinc-400 mb-3 uppercase tracking-wider">{children}</h3>;
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
                className="py-2 pr-4 text-zinc-500 font-medium cursor-pointer hover:text-zinc-300 select-none whitespace-nowrap transition-colors duration-150 ease-out-quart"
                onClick={() => handleSort(c.key)}
              >
                {c.label} {sortKey === c.key ? (sortDir === "desc" ? "↓" : "↑") : ""}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {sorted.map((r) => (
            <tr key={r.ticker} className="border-b border-zinc-800/50 hover:bg-zinc-800/30 transition-colors duration-150 ease-out-quart">
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

// snake_case gate/factor key → Title Case label
function prettyLabel(s: string): string {
  return s.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

// Factor-score color band: green ≥ 60, amber 40–59, red < 40.
function scoreColor(v: number): string {
  if (v >= 60) return "#10b981";
  if (v >= 40) return "#f59e0b";
  return "#f87171";
}

// ── Factor contribution (horizontal bars: watchlist avg + actionable subset) ────

function FactorContributionChart({ data }: { data: FactorContributionData }) {
  const rows = data.factors.map((f) => ({
    ...f,
    label: prettyLabel(f.name),
  }));
  return (
    <ResponsiveContainer width="100%" height={Math.max(140, rows.length * 46)}>
      <BarChart data={rows} layout="vertical" margin={{ top: 4, right: 12, left: 8, bottom: 0 }}>
        <XAxis
          type="number"
          domain={[0, 100]}
          tick={{ fontSize: 10, fill: "#71717a" }}
        />
        <YAxis
          type="category"
          dataKey="label"
          width={72}
          tick={{ fontSize: 10, fill: "#a1a1aa" }}
        />
        <Tooltip
          contentStyle={{ background: "#18181b", border: "1px solid #3f3f46", borderRadius: 6 }}
          labelStyle={{ color: "#a1a1aa", fontSize: 11 }}
          formatter={(v, name) => [
            typeof v === "number" ? v.toFixed(1) : "—",
            name === "avg_score_all" ? "All" : "Actionable (≥63)",
          ]}
        />
        <Legend
          wrapperStyle={{ fontSize: 10, paddingTop: 4 }}
          formatter={(v) => (v === "avg_score_all" ? "Watchlist avg" : "Actionable (≥63)")}
        />
        <ReferenceLine x={60} stroke="#52525b" strokeDasharray="4 2" />
        <Bar dataKey="avg_score_all" radius={[0, 3, 3, 0]} barSize={11}>
          {rows.map((r) => (
            <Cell key={r.name} fill={scoreColor(r.avg_score_all)} />
          ))}
        </Bar>
        <Bar dataKey="avg_score_actionable" radius={[0, 3, 3, 0]} barSize={11} fill="#818cf8" />
      </BarChart>
    </ResponsiveContainer>
  );
}

// ── Live gate rejections (ranked bars, BacktestPanel style) ─────────────────────

function GateRejectionList({ data }: { data: GateRejectionsData }) {
  const maxCount = data.rejections.length ? data.rejections[0].count : 0;
  return (
    <div className="space-y-1.5">
      {data.rejections.map((r) => (
        <div key={r.gate} className="flex items-center gap-2 text-xs">
          <div className="w-40 shrink-0 text-zinc-400 truncate" title={prettyLabel(r.gate)}>
            {prettyLabel(r.gate)}
          </div>
          <div className="flex-1 bg-zinc-800/50 rounded h-4 overflow-hidden">
            <div
              className="h-full bg-amber-500/70 rounded transition-[width] duration-300 ease-out-quart"
              style={{ width: `${maxCount ? (r.count / maxCount) * 100 : 0}%` }}
            />
          </div>
          <div className="w-8 shrink-0 text-right tabular-nums text-zinc-300">{r.count}</div>
          <div className="w-12 shrink-0 text-right tabular-nums text-zinc-500">
            {r.pct_of_skipped.toFixed(0)}%
          </div>
        </div>
      ))}
    </div>
  );
}

// ── Drawdown-from-peak value (colored, for a System Health chip) ────────────────

function DrawdownValue({ d }: { d: DrawdownData | null }) {
  if (!d || d.snapshot_count < 10 || d.drawdown_pct === null) {
    return <span className="text-zinc-500">Insufficient data</span>;
  }
  const dd = d.drawdown_pct;
  const color = dd < -2 ? "text-red-400" : dd < -1 ? "text-amber-400" : "text-zinc-400";
  return <span className={`tabular-nums ${color}`}>{dd.toFixed(2)}%</span>;
}

// ── Main component ────────────────────────────────────────────────────────────

export function AnalyticsTab() {
  const [data, setData] = useState<AnalyticsData | null>(null);
  const [factorContrib, setFactorContrib] = useState<FactorContributionData | null>(null);
  const [gateRej, setGateRej] = useState<GateRejectionsData | null>(null);
  const [drawdown, setDrawdown] = useState<DrawdownData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    // The additive deep-dives load independently and degrade to empty states on
    // failure so they never break the core analytics sections.
    fetchFactorContribution().then(setFactorContrib).catch(() => setFactorContrib(null));
    fetchGateRejections().then(setGateRej).catch(() => setGateRej(null));
    fetchDrawdown().then(setDrawdown).catch(() => setDrawdown(null));
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
          className="px-3 py-1.5 text-xs text-zinc-200 bg-zinc-800 hover:bg-zinc-700 disabled:opacity-50 rounded-lg
            transition-[background-color,transform] duration-150 ease-out-quart active:scale-[0.98]"
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

      {/* Factor contribution */}
      <div>
        <SectionTitle>Factor Contribution — Current Watchlist Average</SectionTitle>
        <p className="text-[10px] text-zinc-600 mb-3">
          Average factor scores across your watchlist. Low scores indicate factors
          dragging composites below threshold.
        </p>
        {loading ? <ChartSkeleton /> : factorContrib && factorContrib.factors.length > 0 ? (
          <>
            <FactorContributionChart data={factorContrib} />
            <p className="text-[10px] text-zinc-600 mt-1">
              Across {factorContrib.ticker_count} ticker{factorContrib.ticker_count === 1 ? "" : "s"}
              {factorContrib.actionable_count > 0
                ? ` · ${factorContrib.actionable_count} actionable (composite ≥ 63)`
                : ""}.
              Green ≥ 60 · amber 40–59 · red &lt; 40. Reference line = 60.
            </p>
          </>
        ) : !loading && (
          <p className="text-zinc-600 text-xs py-6 text-center">
            No watchlist factor data yet.
          </p>
        )}
      </div>

      {/* Live gate rejections */}
      <div>
        <SectionTitle>Live Gate Rejections — Last 30 Days</SectionTitle>
        {loading ? (
          <div className="space-y-2">{[1, 2, 3].map((i) => <Skeleton key={i} className="h-4 w-full" />)}</div>
        ) : gateRej && gateRej.rejections.length > 0 ? (
          <>
            <GateRejectionList data={gateRej} />
            <p className="text-[10px] text-zinc-600 mt-3">
              {gateRej.total_evaluated} signal{gateRej.total_evaluated === 1 ? "" : "s"} evaluated,{" "}
              {gateRej.total_skipped} skipped (
              {gateRej.total_evaluated
                ? ((gateRej.total_skipped / gateRej.total_evaluated) * 100).toFixed(0)
                : "0"}
              %) in the last {gateRej.period_days} days.
            </p>
          </>
        ) : !loading && (
          <p className="text-zinc-600 text-xs py-6 text-center">
            No skipped signals logged in the last 30 days.
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
              Adjusted weekly via exponential weighted average (α = 0.15):
              tightens when recent win rate &lt; 40%, loosens when &gt; 60%.
              Range: bull 63–80 · bear 75–85.
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
            <HealthChip label="Drawdown from Peak" value={<DrawdownValue d={drawdown} />} />
          </div>
        ) : null}
      </div>
    </div>
  );
}
