"use client";
import { useEffect, useState } from "react";
import { PaperPosition } from "@/lib/types";
import { fetchPaperPositions } from "@/lib/api";

// Surfaces the live Alpaca position for this ticker (if held) — entry, P&L, distance
// to trailing stop. Pure read of /api/paper/positions; renders nothing when not held.

function usd(n: number) {
  return `$${n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function Metric({ label, value, tone }: { label: string; value: string; tone?: "pos" | "neg" }) {
  const color = tone === "pos" ? "text-emerald-400" : tone === "neg" ? "text-red-400" : "text-zinc-100";
  return (
    <div className="flex flex-col">
      <span className="text-[10px] text-zinc-500 uppercase tracking-wider">{label}</span>
      <span className={`text-sm font-semibold tabular-nums tracking-tight ${color}`}>{value}</span>
    </div>
  );
}

export function PositionBanner({ ticker }: { ticker: string }) {
  const [pos, setPos] = useState<PaperPosition | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchPaperPositions()
      .then((d) => {
        if (cancelled) return;
        const found = d.available && d.positions
          ? d.positions.find((p) => p.ticker === ticker) ?? null
          : null;
        setPos(found);
      })
      .catch(() => { if (!cancelled) setPos(null); });
    return () => { cancelled = true; };
  }, [ticker]);

  if (!pos) return null;

  const pnlPos = pos.pnl_pct >= 0;
  const stop = pos.trailing_stop;
  const stopDistUsd = stop != null ? pos.current_price - stop : null;
  const stopDistPct = stop != null && pos.current_price ? (stopDistUsd! / pos.current_price) * 100 : null;

  return (
    <div className="rounded-xl border border-sky-800/40 bg-sky-950/20 px-5 py-3">
      <div className="flex items-center gap-2 mb-2.5">
        <span className="inline-block h-2 w-2 rounded-full bg-sky-400" aria-hidden />
        <span className="text-xs font-semibold text-sky-300 tracking-tight">Currently held</span>
        <span className="text-[11px] text-zinc-500">
          {pos.qty} {pos.qty === 1 ? "share" : "shares"} · {usd(pos.market_value)}
        </span>
      </div>
      <div className="flex items-center gap-x-8 gap-y-3 flex-wrap">
        <Metric label="Entry" value={usd(pos.entry_price)} />
        <Metric label="Current" value={usd(pos.current_price)} />
        <Metric label="P&L" value={`${pnlPos ? "+" : ""}${pos.pnl_pct.toFixed(2)}%`} tone={pnlPos ? "pos" : "neg"} />
        <Metric label="Days held" value={String(pos.days_held)} />
        <Metric
          label="To trailing stop"
          value={
            stop != null && stopDistUsd != null && stopDistPct != null
              ? `${usd(stopDistUsd)} (${stopDistPct.toFixed(1)}%)`
              : "—"
          }
          tone={stopDistUsd != null ? (stopDistUsd >= 0 ? "pos" : "neg") : undefined}
        />
      </div>
    </div>
  );
}
