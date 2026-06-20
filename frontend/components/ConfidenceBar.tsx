"use client";
import { Signal } from "@/lib/types";

const COLOR: Record<Signal, string> = {
  BUY:  "bg-emerald-500",
  SELL: "bg-red-500",
  HOLD: "bg-amber-400",
};

export function ConfidenceBar({ confidence, signal }: { confidence: number; signal: Signal }) {
  const pct = Math.round(confidence * 100);
  return (
    <div className="w-full">
      <div className="flex justify-between text-xs mb-1.5">
        <span className="text-zinc-500">Confidence</span>
        <span className="text-zinc-300 font-medium tabular-nums">{pct}%</span>
      </div>
      <div className="h-1.5 rounded-full bg-zinc-800 overflow-hidden">
        <div
          className={`h-full rounded-full transition-[width] duration-500 ease-out-quart ${COLOR[signal]}`}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}
