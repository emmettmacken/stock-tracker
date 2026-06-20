"use client";
import { useState } from "react";
import {
  LineChart, Line, AreaChart, Area,
  BarChart, Bar, Cell,
  XAxis, YAxis, Tooltip, Legend,
  ResponsiveContainer, ReferenceLine, CartesianGrid,
} from "recharts";
import { PortfolioBacktestResult } from "@/lib/types";
import { fetchPortfolioBacktest } from "@/lib/api";
import { EfficientFrontierChart } from "./EfficientFrontierChart";

interface Props {
  tickers: string[];
  capital: number;
}

function StatCard({
  label, value, sub, positive,
}: {
  label: string; value: string; sub?: string; positive?: boolean;
}) {
  const color =
    positive === undefined ? "text-zinc-100" : positive ? "text-emerald-400" : "text-red-400";
  return (
    <div className="bg-zinc-800/50 rounded-lg p-3">
      <div className="text-zinc-500 text-[10px] mb-1">{label}</div>
      <div className={`text-base font-bold tabular-nums ${color}`}>{value}</div>
      {sub && <div className="text-zinc-600 text-[10px] mt-0.5 tabular-nums">{sub}</div>}
    </div>
  );
}

function fmt(n: number, suffix = "%", decimals = 2) {
  return `${n >= 0 ? "+" : ""}${n.toFixed(decimals)}${suffix}`;
}

function computeDrawdown(values: number[]) {
  let peak = values[0] ?? 1;
  return values.map((v) => {
    peak = Math.max(peak, v);
    return ((v - peak) / peak) * 100;
  });
}

function computeMonthlyReturns(curve: { date: string; value: number }[]) {
  const groups: Record<string, number[]> = {};
  curve.forEach((pt) => {
    const m = pt.date.substring(0, 7);
    (groups[m] ??= []).push(pt.value);
  });
  return Object.values(groups).map((vals) => ({
    return: ((vals[vals.length - 1] - vals[0]) / vals[0]) * 100,
  }));
}

export function PortfolioBacktestPanel({ tickers, capital }: Props) {
  const [result, setResult] = useState<PortfolioBacktestResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function run() {
    setLoading(true);
    setError(null);
    try {
      const data = await fetchPortfolioBacktest({ tickers, capital });
      setResult(data);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Backtest failed");
    } finally {
      setLoading(false);
    }
  }

  if (!result && !loading && !error) {
    return (
      <div className="text-center py-8">
        <p className="text-zinc-500 text-xs mb-4">
          Walk-forward portfolio backtest across {tickers.length} ticker{tickers.length !== 1 ? "s" : ""}.
          Train 252d → test 21d, rolling. Capital allocated by vol-targeted signals.
        </p>
        <button
          onClick={run}
          disabled={tickers.length === 0}
          className="px-5 py-2 bg-zinc-100 hover:bg-white disabled:opacity-40 disabled:cursor-not-allowed text-zinc-900 text-sm font-medium rounded-lg
            transition-[background-color,transform] duration-150 ease-out-quart active:scale-[0.98]"
        >
          Run Portfolio Backtest
        </button>
      </div>
    );
  }

  if (loading) {
    return (
      <div className="flex flex-col items-center gap-3 py-10 text-zinc-500 text-sm">
        <span className="inline-block w-6 h-6 border-2 border-zinc-600 border-t-zinc-300 rounded-full animate-spin" />
        Running walk-forward backtest across {tickers.join(", ")}…
        <span className="text-xs text-zinc-600">This may take 15–30 seconds</span>
      </div>
    );
  }

  if (error) {
    return (
      <div className="py-6 text-center">
        <p className="text-red-400 text-sm mb-2">{error}</p>
        <button onClick={run} className="text-zinc-400 hover:text-white text-xs underline">
          Retry
        </button>
      </div>
    );
  }

  const d = result!;
  const curve = d.equity_curve;

  // Plain-English recap from numbers the backtest already returned.
  const months = (() => {
    if (curve.length < 2) return null;
    const ms = new Date(curve[curve.length - 1].date).getTime() - new Date(curve[0].date).getTime();
    return Math.max(1, Math.round(ms / (1000 * 60 * 60 * 24 * 30.44)));
  })();

  // Normalise to 100 at start
  const chartData = curve.map((pt) => ({
    date: pt.date,
    Portfolio: parseFloat(((pt.value / capital) * 100).toFixed(2)),
    SPY: pt.spy != null ? parseFloat(((pt.spy / capital) * 100).toFixed(2)) : null,
  }));

  const ddValues = computeDrawdown(curve.map((p) => p.value));
  const ddData = curve.map((pt, i) => ({
    date: pt.date,
    Drawdown: parseFloat(ddValues[i].toFixed(2)),
  }));

  const monthlyRets = computeMonthlyReturns(curve);
  const bestMonth = monthlyRets.length ? Math.max(...monthlyRets.map((m) => m.return)) : 0;
  const worstMonth = monthlyRets.length ? Math.min(...monthlyRets.map((m) => m.return)) : 0;

  const contribData = Object.entries(d.per_ticker_contrib)
    .map(([ticker, contrib]) => ({ ticker, contribution: parseFloat(contrib.toFixed(2)) }))
    .sort((a, b) => b.contribution - a.contribution);

  const tickFormatter = (v: string) => v.substring(5); // MM-DD

  return (
    <div className="space-y-6 text-xs">
      {/* Plain-English summary */}
      <p className="text-sm text-zinc-300 leading-relaxed">
        Over the past {months ?? "—"} months, this portfolio would have returned{" "}
        <span className={d.total_return_pct >= 0 ? "text-emerald-400 font-medium" : "text-red-400 font-medium"}>
          {fmt(d.total_return_pct)}
        </span>
        {d.spy_return_pct != null && (
          <>
            {" "}versus SPY&apos;s{" "}
            <span className={d.spy_return_pct >= 0 ? "text-emerald-400 font-medium" : "text-red-400 font-medium"}>
              {fmt(d.spy_return_pct)}
            </span>
          </>
        )}
        , with a max drawdown of{" "}
        <span className="text-zinc-100 font-medium">{d.max_drawdown_pct.toFixed(1)}%</span>.
      </p>

      {/* Stat cards */}
      <div className="grid grid-cols-3 gap-2">
        <StatCard
          label="Total Return"
          value={fmt(d.total_return_pct)}
          positive={d.total_return_pct >= 0}
        />
        <StatCard
          label="SPY Return"
          value={d.spy_return_pct != null ? fmt(d.spy_return_pct) : "—"}
          positive={(d.spy_return_pct ?? 0) >= 0}
        />
        <StatCard
          label="Sharpe Ratio"
          value={d.sharpe_ratio.toFixed(2)}
          sub="annualised"
          positive={d.sharpe_ratio >= 0}
        />
        <StatCard
          label="Max Drawdown"
          value={`${d.max_drawdown_pct.toFixed(1)}%`}
          positive={d.max_drawdown_pct > -10}
        />
        <StatCard
          label="Best Month"
          value={fmt(bestMonth)}
          positive={bestMonth >= 0}
        />
        <StatCard
          label="Worst Month"
          value={fmt(worstMonth)}
          positive={worstMonth >= 0}
        />
      </div>

      {/* Equity curve */}
      <div>
        <h4 className="font-semibold text-zinc-400 uppercase tracking-wide mb-3 text-[10px]">
          Equity Curve (base = 100)
        </h4>
        <ResponsiveContainer width="100%" height={200}>
          <LineChart data={chartData} margin={{ top: 4, right: 4, bottom: 0, left: -20 }}>
            <XAxis dataKey="date" tick={false} axisLine={false} tickLine={false} />
            <YAxis tick={{ fill: "#71717a", fontSize: 10 }} axisLine={false} tickLine={false} />
            <Tooltip
              contentStyle={{ background: "#18181b", border: "1px solid #3f3f46", borderRadius: 6 }}
              labelStyle={{ color: "#e4e4e7", fontSize: 10 }}
              itemStyle={{ fontSize: 10 }}
              formatter={(v: number) => [`${v.toFixed(2)}`, ""]}
            />
            <Legend wrapperStyle={{ fontSize: 10, paddingTop: 4 }} iconType="line" />
            <ReferenceLine y={100} stroke="#3f3f46" strokeDasharray="3 3" />
            <Line type="monotone" dataKey="Portfolio" stroke="#10b981" strokeWidth={1.5} dot={false} />
            <Line type="monotone" dataKey="SPY" stroke="#a1a1aa" strokeWidth={1.5} dot={false} strokeDasharray="4 2" />
          </LineChart>
        </ResponsiveContainer>
      </div>

      {/* Drawdown chart */}
      <div>
        <h4 className="font-semibold text-zinc-400 uppercase tracking-wide mb-3 text-[10px]">
          Portfolio Drawdown
        </h4>
        <ResponsiveContainer width="100%" height={120}>
          <AreaChart data={ddData} margin={{ top: 4, right: 4, bottom: 0, left: -20 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#27272a" />
            <XAxis dataKey="date" tick={false} axisLine={false} tickLine={false} />
            <YAxis
              tick={{ fill: "#71717a", fontSize: 10 }}
              axisLine={false}
              tickLine={false}
              tickFormatter={(v) => `${v.toFixed(0)}%`}
            />
            <Tooltip
              contentStyle={{ background: "#18181b", border: "1px solid #3f3f46", borderRadius: 6 }}
              labelStyle={{ color: "#e4e4e7", fontSize: 10 }}
              itemStyle={{ fontSize: 10 }}
              formatter={(v: number) => [`${v.toFixed(2)}%`, "Drawdown"]}
            />
            <ReferenceLine y={0} stroke="#3f3f46" />
            <Area
              type="monotone"
              dataKey="Drawdown"
              stroke="#ef4444"
              fill="#ef4444"
              fillOpacity={0.25}
              strokeWidth={1}
              dot={false}
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>

      {/* Per-ticker contribution */}
      {contribData.length > 0 && (
        <div>
          <h4 className="text-[10px] font-semibold text-zinc-500 uppercase tracking-wider mb-3">
            Ticker Contribution to Return
          </h4>
          <ResponsiveContainer width="100%" height={Math.max(60, contribData.length * 32)}>
            <BarChart
              layout="vertical"
              data={contribData}
              margin={{ top: 0, right: 20, bottom: 0, left: 30 }}
            >
              <XAxis
                type="number"
                tick={{ fill: "#71717a", fontSize: 10 }}
                axisLine={false}
                tickLine={false}
                tickFormatter={(v) => `${v > 0 ? "+" : ""}${v.toFixed(0)}%`}
              />
              <YAxis
                type="category"
                dataKey="ticker"
                tick={{ fill: "#a1a1aa", fontSize: 10 }}
                axisLine={false}
                tickLine={false}
                width={40}
              />
              <Tooltip
                contentStyle={{ background: "#18181b", border: "1px solid #3f3f46", borderRadius: 6 }}
                itemStyle={{ fontSize: 10 }}
                formatter={(v: number) => [`${v >= 0 ? "+" : ""}${v.toFixed(2)}%`, "Contribution"]}
              />
              <ReferenceLine x={0} stroke="#3f3f46" />
              <Bar dataKey="contribution" radius={[0, 3, 3, 0]}>
                {contribData.map((entry) => (
                  <Cell
                    key={entry.ticker}
                    fill={entry.contribution >= 0 ? "#10b981" : "#ef4444"}
                  />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}

      {/* Efficient frontier */}
      {d.efficient_frontier.length > 0 && (
        <div>
          <h4 className="text-[10px] font-semibold text-zinc-500 uppercase tracking-wider mb-3">
            Efficient Frontier (500 Monte Carlo Portfolios)
          </h4>
          <EfficientFrontierChart points={d.efficient_frontier} />
        </div>
      )}

      <button
        onClick={run}
        className="text-zinc-600 hover:text-zinc-400 text-[10px] underline"
      >
        Re-run backtest
      </button>
    </div>
  );
}
