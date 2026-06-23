"use client";
import { useEffect } from "react";
import { TradingMode } from "@/lib/api";
import { useTradingSettings } from "@/lib/useTradingSettings";
import { ToggleSwitch } from "./ToggleSwitch";

const STARTING_BALANCE_LABEL = "$100,000";

function ModeRadio({
  selected,
  value,
  title,
  description,
  onSelect,
  disabled,
}: {
  selected: boolean;
  value: TradingMode;
  title: string;
  description: string;
  onSelect: (v: TradingMode) => void;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={() => onSelect(value)}
      disabled={disabled}
      className={`w-full text-left flex gap-2.5 rounded-lg border px-3 py-2.5 transition-colors duration-150 disabled:opacity-50 ${
        selected
          ? "border-zinc-500 bg-zinc-800/50"
          : "border-zinc-800 hover:border-zinc-700"
      }`}
    >
      <span
        className={`mt-0.5 inline-flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded-full border ${
          selected ? "border-zinc-300" : "border-zinc-600"
        }`}
      >
        {selected && <span className="h-1.5 w-1.5 rounded-full bg-zinc-100" />}
      </span>
      <span>
        <span className="block text-xs font-medium text-zinc-200">{title}</span>
        <span className="block text-[11px] leading-relaxed text-zinc-500 mt-0.5">{description}</span>
      </span>
    </button>
  );
}

export function PortfolioSettingsModal({
  onClose,
  showNetDeposits,
  onShowNetDepositsChange,
}: {
  onClose: () => void;
  showNetDeposits: boolean;
  onShowNetDepositsChange: (v: boolean) => void;
}) {
  // Own hook instance — fetches GET /api/settings/trading when the modal mounts (opens).
  const { settings, loading, error, update } = useTradingSettings();

  // Close on Escape.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const enabled = settings?.automated_trading_enabled ?? true;
  const mode = settings?.automated_trading_mode ?? "all";

  // Optimistic — useTradingSettings reverts on error. Swallow the rejection here so an
  // unhandled promise doesn't surface; the hook already records the error message.
  const setEnabled = (next: boolean) => { void update({ automated_trading_enabled: next }).catch(() => {}); };
  const setMode = (next: TradingMode) => { void update({ automated_trading_mode: next }).catch(() => {}); };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="w-full max-w-md bg-zinc-900 border border-zinc-800 rounded-xl p-5 space-y-5 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between">
          <h3 className="text-base font-semibold text-white tracking-tight">Portfolio Settings</h3>
          <button
            onClick={onClose}
            aria-label="Close"
            className="text-zinc-500 hover:text-zinc-200 transition-colors duration-150"
          >
            <svg viewBox="0 0 20 20" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth={1.6}>
              <path d="M5 5l10 10M15 5L5 15" strokeLinecap="round" />
            </svg>
          </button>
        </div>

        {error && <p className="text-xs text-red-400">{error}</p>}

        {/* Automated Trading */}
        <section className="space-y-3">
          <div className="flex items-start justify-between gap-3">
            <div>
              <h4 className="text-sm font-medium text-zinc-100">Automated Trading</h4>
              <p className="text-[11px] text-zinc-500 mt-0.5 leading-relaxed">
                Control whether the system places trades automatically.
              </p>
            </div>
            <div className="flex items-center gap-2 shrink-0">
              <span className={`text-xs font-medium ${enabled ? "text-emerald-400" : "text-red-400"}`}>
                {enabled ? "Enabled" : "Paused"}
              </span>
              <ToggleSwitch
                checked={enabled}
                disabled={loading}
                onChange={setEnabled}
                onColor="bg-emerald-500"
                offColor="bg-red-500"
                label="Automated trading"
              />
            </div>
          </div>

          {!enabled && (
            <div className="space-y-2 pt-1">
              <ModeRadio
                value="all"
                selected={mode === "all"}
                onSelect={setMode}
                disabled={loading}
                title="Pause everything"
                description="No new entries and no automatic exits (stop loss, 21-day sell). You can still close positions manually."
              />
              <ModeRadio
                value="entries_only"
                selected={mode === "entries_only"}
                onSelect={setMode}
                disabled={loading}
                title="Pause new entries only"
                description="No new entries, but stop loss, 21-day sell, and macro protection still run automatically."
              />
            </div>
          )}
        </section>

        <div className="border-t border-zinc-800" />

        {/* Net Deposits */}
        <section className="space-y-1.5">
          <div className="flex items-start justify-between gap-3">
            <div>
              <h4 className="text-sm font-medium text-zinc-100">Show Net Deposits Line</h4>
              <p className="text-[11px] text-zinc-500 mt-0.5 leading-relaxed">
                Shows a dotted line on the equity curve at your starting balance ({STARTING_BALANCE_LABEL})
                so you can see performance relative to your initial deposit.
              </p>
            </div>
            <div className="shrink-0 pt-0.5">
              <ToggleSwitch
                checked={showNetDeposits}
                onChange={onShowNetDepositsChange}
                onColor="bg-emerald-500"
                offColor="bg-zinc-600"
                label="Show net deposits line"
              />
            </div>
          </div>
        </section>
      </div>
    </div>
  );
}
