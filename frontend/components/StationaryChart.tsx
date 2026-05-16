"use client";
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  Cell,
  ResponsiveContainer,
} from "recharts";

const COLORS = ["#ef4444", "#f97316", "#a1a1aa", "#34d399", "#10b981"];
const LABELS = ["S.Down", "Down", "Flat", "Up", "S.Up"];

export function StationaryChart({
  distribution,
  currentStateIdx,
}: {
  distribution: number[];
  currentStateIdx: number;
}) {
  const data = distribution.map((val, i) => ({
    state: LABELS[i],
    probability: parseFloat((val * 100).toFixed(2)),
    isCurrent: i === currentStateIdx,
  }));

  return (
    <ResponsiveContainer width="100%" height={160}>
      <BarChart data={data} margin={{ top: 4, right: 4, bottom: 0, left: -20 }}>
        <XAxis dataKey="state" tick={{ fill: "#a1a1aa", fontSize: 11 }} axisLine={false} tickLine={false} />
        <YAxis tick={{ fill: "#71717a", fontSize: 10 }} axisLine={false} tickLine={false} unit="%" />
        <Tooltip
          contentStyle={{ background: "#18181b", border: "1px solid #3f3f46", borderRadius: 6 }}
          labelStyle={{ color: "#e4e4e7", fontSize: 12 }}
          itemStyle={{ color: "#a1a1aa", fontSize: 12 }}
          formatter={(v) => [`${Number(v).toFixed(2)}%`, "Probability"]}
        />
        <Bar dataKey="probability" radius={[3, 3, 0, 0]}>
          {data.map((entry, i) => (
            <Cell
              key={i}
              fill={COLORS[i]}
              opacity={entry.isCurrent ? 1 : 0.55}
              stroke={entry.isCurrent ? "#fff" : "none"}
              strokeWidth={entry.isCurrent ? 1.5 : 0}
            />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}
