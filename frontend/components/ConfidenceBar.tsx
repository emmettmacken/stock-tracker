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
      <div className="flex justify-between text-xs text-zinc-400 mb-1">
        <span>Confidence</span>
        <span>{pct}%</span>
      </div>
      <div className="h-1.5 rounded-full bg-zinc-700 overflow-hidden">
        <div
          className={`h-full rounded-full transition-all duration-500 ${COLOR[signal]}`}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}
