"use client";
import { useState } from "react";
import {
  LineChart, Line, XAxis, YAxis, Tooltip,
  Legend, ResponsiveContainer, ReferenceLine,
} from "recharts";
import { BacktestData } from "@/lib/types";
import { fetchBacktest } from "@/lib/api";

function StatCard({ label, value, sub, positive }: {
  label: string; value: string; sub?: string; positive?: boolean;
}) {
  const color = positive === undefined ? "text-zinc-100"
    : positive ? "text-emerald-400" : "text-red-400";
  return (
    <div className="bg-zinc-800/50 rounded-lg p-3">
      <div className="text-zinc-500 text-xs mb-1">{label}</div>
      <div className={`text-lg font-bold tabular-nums ${color}`}>{value}</div>
      {sub && <div className="text-zinc-600 text-[10px] mt-0.5 tabular-nums">{sub}</div>}
    </div>
  );
}

function fmt(n: number, suffix = "%") {
  return `${n >= 0 ? "+" : ""}${n.toFixed(2)}${suffix}`;
}

export function BacktestPanel({ ticker }: { ticker: string }) {
  const [result, setResult] = useState<BacktestData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function run() {
    setLoading(true);
    setError(null);
    try {
      const data = await fetchBacktest(ticker);
      setResult(data);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Backtest failed");
    } finally {
      setLoading(false);
    }
  }

  if (!result && !loading && !error) {
    return (
      <div className="text-center py-6">
        <p className="text-zinc-500 text-xs mb-3">
          Walk-forward backtest over 2-year history. Train 252 days → test 21 days, rolling.
        </p>
        <button
          onClick={run}
          className="px-4 py-2 bg-zinc-100 hover:bg-white text-zinc-900 text-sm font-medium rounded-lg
            transition-[background-color,transform] duration-150 ease-out-quart active:scale-[0.98]"
        >
          Run backtest
        </button>
      </div>
    );
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center py-10 gap-2 text-zinc-500 text-sm">
        <span className="inline-block w-4 h-4 border-2 border-zinc-600 border-t-zinc-300 rounded-full animate-spin" />
        Running walk-forward backtest…
      </div>
    );
  }

  if (error) {
    return (
      <div className="py-4 text-center">
        <p className="text-red-400 text-xs mb-2">{error}</p>
        <button onClick={run} className="text-zinc-400 hover:text-white text-xs underline">
          Retry
        </button>
      </div>
    );
  }

  const d = result!;
  const stratPositive = d.total_strategy_return >= 0;
  const vsBAH = d.total_strategy_return - d.total_bah_return;

  // Plain-English recap built purely from numbers the backtest already returned.
  const months = (() => {
    const c = d.equity_curve;
    if (c.length < 2) return null;
    const ms = new Date(c[c.length - 1].date).getTime() - new Date(c[0].date).getTime();
    return Math.max(1, Math.round(ms / (1000 * 60 * 60 * 24 * 30.44)));
  })();

  // Normalise equity curve to 100 for display
  const chartData = d.equity_curve.map((pt, i) => ({
    i,
    date: pt.date,
    Strategy: parseFloat((pt.strategy * 100).toFixed(2)),
    "Buy & Hold": parseFloat((pt.bah * 100).toFixed(2)),
  }));

  return (
    <div className="space-y-4 text-xs">
      {/* Plain-English summary */}
      <p className="text-sm text-zinc-300 leading-relaxed">
        Over the past {months ?? "—"} months, this strategy would have returned{" "}
        <span className={stratPositive ? "text-emerald-400 font-medium" : "text-red-400 font-medium"}>
          {fmt(d.total_strategy_return)}
        </span>{" "}
        versus buy-and-hold&apos;s{" "}
        <span className={d.total_bah_return >= 0 ? "text-emerald-400 font-medium" : "text-red-400 font-medium"}>
          {fmt(d.total_bah_return)}
        </span>
        , with a max drawdown of{" "}
        <span className="text-zinc-100 font-medium">{d.max_drawdown.toFixed(1)}%</span>.
      </p>

      {/* Summary stats */}
      <div className="grid grid-cols-2 gap-2">
        <StatCard
          label="Strategy Return"
          value={fmt(d.total_strategy_return)}
          sub={`vs B&H: ${fmt(vsBAH)}`}
          positive={stratPositive}
        />
        <StatCard
          label="Buy & Hold"
          value={fmt(d.total_bah_return)}
          positive={d.total_bah_return >= 0}
        />
        <StatCard
          label="Sharpe Ratio"
          value={d.sharpe_ratio.toFixed(2)}
          sub="annualised"
          positive={d.sharpe_ratio >= 0}
        />
        <StatCard
          label="Max Drawdown"
          value={`${d.max_drawdown.toFixed(1)}%`}
          positive={d.max_drawdown > -10}
        />
        <StatCard
          label="Trade Win Rate"
          value={`${d.win_rate_trades.toFixed(1)}%`}
          sub={`${d.num_trades} trades`}
        />
        <StatCard
          label="Test Windows"
          value={String(d.num_windows)}
          sub="21 trading days each"
        />
      </div>

      {/* Equity curve */}
      <div>
        <h4 className="text-[10px] font-semibold text-zinc-500 uppercase tracking-wider mb-2">
          Equity Curve (base = 100)
        </h4>
        <ResponsiveContainer width="100%" height={180}>
          <LineChart data={chartData} margin={{ top: 4, right: 4, bottom: 0, left: -20 }}>
            <XAxis dataKey="date" tick={false} axisLine={false} tickLine={false} />
            <YAxis tick={{ fill: "#71717a", fontSize: 10 }} axisLine={false} tickLine={false} />
            <Tooltip
              contentStyle={{ background: "#18181b", border: "1px solid #3f3f46", borderRadius: 6 }}
              labelStyle={{ color: "#e4e4e7", fontSize: 11 }}
              itemStyle={{ fontSize: 11 }}
              formatter={(v: number) => [`${v.toFixed(2)}`, ""]}
            />
            <Legend
              wrapperStyle={{ fontSize: 11, paddingTop: 4 }}
              iconType="line"
            />
            <ReferenceLine y={100} stroke="#3f3f46" strokeDasharray="3 3" />
            <Line
              type="monotone" dataKey="Strategy"
              stroke="#10b981" strokeWidth={1.5} dot={false}
            />
            <Line
              type="monotone" dataKey="Buy & Hold"
              stroke="#a1a1aa" strokeWidth={1.5} dot={false} strokeDasharray="4 2"
            />
          </LineChart>
        </ResponsiveContainer>
      </div>

      <button
        onClick={run}
        className="text-zinc-600 hover:text-zinc-400 text-[10px] underline"
      >
        Re-run
      </button>
    </div>
  );
}
