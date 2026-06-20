"use client";
import { useEffect, useMemo, useState } from "react";
import { LineChart, Line, YAxis, ResponsiveContainer, Tooltip } from "recharts";
import { PaperAccount, EquityHistory } from "@/lib/types";
import { fetchEquityHistory } from "@/lib/api";

function EquitySparkline() {
  const [hist, setHist] = useState<EquityHistory | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchEquityHistory(30)
      .then((d) => { if (!cancelled) setHist(d); })
      .catch(() => { if (!cancelled) setHist(null); });
    return () => { cancelled = true; };
  }, []);

  const data = useMemo(() => hist?.points ?? [], [hist]);
  const trendUp = useMemo(() => {
    if (data.length < 2) return true;
    return data[data.length - 1].equity >= data[0].equity;
  }, [data]);

  if (!hist || !hist.available || data.length < 2) return null;
  const color = trendUp ? "#10b981" : "#ef4444";

  return (
    <div className="flex flex-col items-end gap-0.5">
      <span className="text-[10px] text-zinc-500 uppercase tracking-wider">
        30d equity{hist.approximate ? " ≈" : ""}
      </span>
      <div className="w-32 h-9">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data} margin={{ top: 2, right: 2, bottom: 2, left: 2 }}>
            <YAxis hide domain={["dataMin", "dataMax"]} />
            <Tooltip
              contentStyle={{ background: "#18181b", border: "1px solid #3f3f46", borderRadius: 6, padding: "2px 6px" }}
              labelStyle={{ color: "#a1a1aa", fontSize: 9 }}
              itemStyle={{ fontSize: 10 }}
              formatter={(v: number) => [`€${v.toLocaleString("en-IE", { maximumFractionDigits: 0 })}`, "Equity"]}
              labelFormatter={(l) => String(l)}
            />
            <Line type="monotone" dataKey="equity" stroke={color} strokeWidth={1.4} dot={false} />
          </LineChart>
        </ResponsiveContainer>
      </div>
      {hist.approximate && (
        <span className="text-[9px] text-zinc-600" title="Reconstructed from closed trades — approximate">
          approx.
        </span>
      )}
    </div>
  );
}

function Stat({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="flex flex-col">
      <span className="text-[10px] text-zinc-500 uppercase tracking-wider">{label}</span>
      <span className="text-lg font-bold text-zinc-100 tabular-nums tracking-tight">{value}</span>
      {sub && <span className="text-[10px] text-zinc-600">{sub}</span>}
    </div>
  );
}

function fmt(n: number) {
  return n.toLocaleString("en-IE", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

interface Props {
  data: PaperAccount | null;
  lastUpdated: Date | null;
}

export function AccountSummary({ data, lastUpdated }: Props) {
  if (!data) {
    return (
      <div className="animate-pulse h-14 bg-zinc-800/50 rounded-xl" />
    );
  }

  if (!data.available) {
    return (
      <div className="bg-zinc-900 border border-zinc-800 rounded-xl px-4 py-3 text-xs text-zinc-500">
        Alpaca paper account not connected —{" "}
        <span className="text-zinc-400">add ALPACA_API_KEY + ALPACA_SECRET_KEY to your backend .env</span>
      </div>
    );
  }

  return (
    <div className="bg-zinc-900 border border-zinc-800 rounded-xl px-5 py-3 flex items-center gap-8 flex-wrap">
      <Stat label="Equity" value={`$${fmt(data.equity ?? 0)}`} />
      <Stat label="Cash" value={`$${fmt(data.cash ?? 0)}`} />
      <Stat label="Buying Power" value={`$${fmt(data.buying_power ?? 0)}`} />
      <Stat label="Open Positions" value={String(data.positions_count ?? 0)} />
      <div className="ml-auto flex flex-col items-end gap-1.5">
        <EquitySparkline />
        <div className="inline-flex items-center gap-1.5 text-[10px] text-zinc-600">
          <span className="relative inline-flex h-1.5 w-1.5">
            <span className="absolute inline-flex h-full w-full rounded-full bg-emerald-500/60 motion-safe:animate-ping" />
            <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-emerald-500" />
          </span>
          {lastUpdated ? `Updated ${lastUpdated.toLocaleTimeString()} · ` : ""}auto-refreshes every 60s
        </div>
      </div>
    </div>
  );
}
