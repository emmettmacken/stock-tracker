"use client";
import { useState } from "react";
import { useTradingSettings } from "@/lib/useTradingSettings";

// Visible automated-trading status for the Automation page. Reflects the same state the
// Portfolio settings modal controls (refetched on window focus). Shows a Resume button
// whenever trading is paused.
export function TradingStatusBanner() {
  const { settings, loading, error, update } = useTradingSettings();
  const [resuming, setResuming] = useState(false);

  if (loading && !settings) return null;
  if (error && !settings) return null;
  if (!settings) return null;

  const enabled = settings.automated_trading_enabled;
  const mode = settings.automated_trading_mode;

  const { dot, text } = enabled
    ? { dot: "bg-emerald-500", text: "Automated trading active" }
    : mode === "all"
      ? { dot: "bg-red-500", text: "Automated trading paused — no trades will be placed" }
      : { dot: "bg-amber-500", text: "New entries paused — exits still active" };

  async function handleResume() {
    setResuming(true);
    try {
      await update({ automated_trading_enabled: true });
    } catch { /* hook records the error and reverts */ }
    finally { setResuming(false); }
  }

  return (
    <div className="flex items-center justify-between gap-3 rounded-lg border border-zinc-800 bg-zinc-900 px-4 py-2.5">
      <div className="flex items-center gap-2.5 min-w-0">
        <span className={`relative inline-flex h-2.5 w-2.5 shrink-0 rounded-full ${dot}`}>
          {enabled && <span className={`absolute inset-0 rounded-full ${dot} animate-ping opacity-60`} />}
        </span>
        <span className="text-sm text-zinc-200 truncate">{text}</span>
      </div>
      {!enabled && (
        <button
          onClick={handleResume}
          disabled={resuming}
          className="shrink-0 rounded-md bg-emerald-500 px-3 py-1.5 text-xs font-semibold text-white hover:bg-emerald-400 disabled:opacity-50 transition-colors duration-150 ease-out-quart"
        >
          {resuming ? "Resuming…" : "Resume trading"}
        </button>
      )}
    </div>
  );
}
