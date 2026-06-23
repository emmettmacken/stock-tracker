"use client";
import { useEffect } from "react";

// Confirmation shown before locking a position. Unlocking needs no confirmation, so
// this modal is only used for the unlocked → locked transition.
export function LockPositionModal({
  ticker,
  submitting,
  error,
  onConfirm,
  onCancel,
}: {
  ticker: string;
  submitting: boolean;
  error: string | null;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  // Close on Escape.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape" && !submitting) onCancel(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onCancel, submitting]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm"
      onClick={() => { if (!submitting) onCancel(); }}
    >
      <div
        className="w-full max-w-sm bg-zinc-900 border border-zinc-800 rounded-xl p-5 space-y-4 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <h3 className="text-base font-semibold text-white tracking-tight">
          Lock {ticker}?
        </h3>
        <p className="text-xs leading-relaxed text-zinc-400">
          Locking this position means the system will not automatically close it — no
          stop loss, no 21-day exit, no macro protection will apply. You can still close
          it manually at any time. The system will log a warning each time an automatic
          close is skipped.
        </p>

        {error && <p className="text-xs text-red-400">{error}</p>}

        <div className="flex gap-2 pt-1">
          <button
            onClick={onCancel}
            disabled={submitting}
            className="flex-1 rounded-lg border border-zinc-700 px-3 py-2 text-xs font-medium text-zinc-300 hover:bg-zinc-800 disabled:opacity-50 transition-colors duration-150"
          >
            Cancel
          </button>
          <button
            onClick={onConfirm}
            disabled={submitting}
            className="flex-[2] rounded-lg bg-red-500 px-3 py-2 text-xs font-semibold text-white hover:bg-red-400 disabled:opacity-40 disabled:cursor-not-allowed transition-colors duration-150"
          >
            {submitting ? "Locking…" : "Lock Position"}
          </button>
        </div>
      </div>
    </div>
  );
}
