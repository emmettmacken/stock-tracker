"use client";
import { PaperAccount } from "@/lib/types";

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
