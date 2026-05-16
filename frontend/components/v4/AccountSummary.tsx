"use client";
import { useState, useEffect, useRef } from "react";
import { PaperAccount } from "@/lib/types";
import { fetchPaperAccount } from "@/lib/api";

function Stat({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="flex flex-col">
      <span className="text-[10px] text-zinc-500 uppercase tracking-widest">{label}</span>
      <span className="text-lg font-bold text-zinc-100 tabular-nums">{value}</span>
      {sub && <span className="text-[10px] text-zinc-600">{sub}</span>}
    </div>
  );
}

function fmt(n: number) {
  return n.toLocaleString("en-IE", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

export function AccountSummary() {
  const [data, setData] = useState<PaperAccount | null>(null);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);

  function load() {
    fetchPaperAccount()
      .then((d) => { setData(d); setLastUpdated(new Date()); })
      .catch(() => {});
  }

  useEffect(() => {
    load();
    const id = setInterval(load, 60_000);
    return () => clearInterval(id);
  }, []);

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
      <div className="ml-auto text-[10px] text-zinc-700 self-end">
        {lastUpdated ? `Updated ${lastUpdated.toLocaleTimeString()}` : ""}
        <span className="ml-2 text-zinc-600">· auto-refreshes every 60s</span>
      </div>
    </div>
  );
}
