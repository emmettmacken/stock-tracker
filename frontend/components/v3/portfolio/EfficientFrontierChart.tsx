"use client";
import {
  ScatterChart, Scatter, XAxis, YAxis, Tooltip,
  ResponsiveContainer, CartesianGrid, Legend,
} from "recharts";
import { EfficientFrontierPoint } from "@/lib/types";

interface Props {
  points: EfficientFrontierPoint[];
}

type XYPoint = { x: number; y: number };

function CustomTooltip({ active, payload }: { active?: boolean; payload?: { payload: XYPoint }[] }) {
  if (!active || !payload?.length) return null;
  const pt = payload[0].payload;
  return (
    <div className="bg-zinc-900 border border-zinc-700 rounded-lg px-3 py-2 text-xs">
      <div className="text-zinc-400">Return: <span className="text-zinc-200 font-semibold">{pt.y.toFixed(1)}%</span></div>
      <div className="text-zinc-400">Volatility: <span className="text-zinc-200 font-semibold">{pt.x.toFixed(1)}%</span></div>
      <div className="text-zinc-400">Sharpe: <span className="text-zinc-200 font-semibold">
        {pt.x > 0 ? (pt.y / pt.x).toFixed(2) : "—"}
      </span></div>
    </div>
  );
}

export function EfficientFrontierChart({ points }: Props) {
  if (!points.length) return (
    <p className="text-zinc-600 text-xs">No efficient frontier data — portfolio needs 2+ tickers.</p>
  );

  const minVarIdx = points.reduce((mi, p, i) => p.volatility < points[mi].volatility ? i : mi, 0);
  const maxSharpeIdx = points.reduce((mi, p, i) => {
    const s = p.volatility > 0 ? p.return / p.volatility : -Infinity;
    const ms = points[mi].volatility > 0 ? points[mi].return / points[mi].volatility : -Infinity;
    return s > ms ? i : mi;
  }, 0);

  const regular: XYPoint[] = [];
  const minVar: XYPoint[] = [];
  const maxSharpe: XYPoint[] = [];

  points.forEach((p, i) => {
    const pt = { x: p.volatility, y: p.return };
    if (i === minVarIdx) minVar.push(pt);
    else if (i === maxSharpeIdx) maxSharpe.push(pt);
    else regular.push(pt);
  });

  return (
    <div>
      <ResponsiveContainer width="100%" height={290}>
        <ScatterChart margin={{ top: 10, right: 10, bottom: 36, left: 10 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#27272a" />
          <XAxis
            type="number"
            dataKey="x"
            name="Volatility"
            unit="%"
            tick={{ fill: "#71717a", fontSize: 10 }}
            axisLine={false}
            tickLine={false}
            label={{ value: "Volatility (ann. %)", position: "insideBottom", offset: -10, fill: "#52525b", fontSize: 10 }}
          />
          <YAxis
            type="number"
            dataKey="y"
            name="Return"
            unit="%"
            tick={{ fill: "#71717a", fontSize: 10 }}
            axisLine={false}
            tickLine={false}
            width={45}
          />
          <Tooltip content={<CustomTooltip />} cursor={{ strokeDasharray: "3 3" }} />
          <Legend
            verticalAlign="bottom"
            wrapperStyle={{ fontSize: 10, paddingTop: 12 }}
          />
          <Scatter
            name="Monte Carlo"
            data={regular}
            fill="#3f3f46"
            opacity={0.6}
            r={2}
          />
          <Scatter
            name="Min Variance"
            data={minVar}
            fill="#38bdf8"
            opacity={1}
            r={6}
          />
          <Scatter
            name="Max Sharpe"
            data={maxSharpe}
            fill="#10b981"
            opacity={1}
            r={6}
          />
        </ScatterChart>
      </ResponsiveContainer>
      <div className="flex gap-4 mt-4 pt-3 border-t border-zinc-800 text-[10px] text-zinc-500 justify-center">
        <span>
          <span className="text-sky-400 font-bold">●</span> Min Variance: {points[minVarIdx].volatility.toFixed(1)}% vol
        </span>
        <span>
          <span className="text-emerald-400 font-bold">●</span> Max Sharpe:{" "}
          {(points[maxSharpeIdx].return / Math.max(points[maxSharpeIdx].volatility, 0.01)).toFixed(2)} ratio
        </span>
      </div>
    </div>
  );
}
