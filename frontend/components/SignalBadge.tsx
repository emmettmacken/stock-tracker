"use client";
import { Signal } from "@/lib/types";

const CONFIG: Record<Signal, { bg: string; text: string; label: string }> = {
  BUY:  { bg: "bg-emerald-500", text: "text-white", label: "BUY" },
  SELL: { bg: "bg-red-500",     text: "text-white", label: "SELL" },
  HOLD: { bg: "bg-amber-400",   text: "text-black", label: "HOLD" },
};

export function SignalBadge({ signal }: { signal: Signal }) {
  const c = CONFIG[signal];
  return (
    <span className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-bold tracking-wide ${c.bg} ${c.text}`}>
      {c.label}
    </span>
  );
}
