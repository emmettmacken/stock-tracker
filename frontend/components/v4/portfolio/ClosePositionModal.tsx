"use client";
import { useEffect, useMemo, useState } from "react";
import { closePosition } from "@/lib/api";
import { fmtUSD } from "@/lib/format";

// Format a share quantity for display: up to 6 decimals, trailing zeros trimmed.
function fmtShares(n: number): string {
  if (!Number.isFinite(n)) return "0";
  return Number(n.toFixed(6)).toString();
}

type Mode = "shares" | "amount";

export function ClosePositionModal({
  ticker,
  currentPrice,
  shares,
  onClose,
  onSuccess,
}: {
  ticker: string;
  currentPrice: number;
  shares: number;          // full held share count
  onClose: () => void;
  onSuccess: (qtyLabel: string) => void;
}) {
  const [mode, setMode] = useState<Mode>("shares");
  const [sharesInput, setSharesInput] = useState(String(shares));
  const [amountInput, setAmountInput] = useState((shares * currentPrice).toFixed(2));
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Close on Escape.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape" && !submitting) onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, submitting]);

  // Effective sell quantity (in shares) derived from the active input.
  const qty = useMemo(() => {
    if (mode === "shares") return parseFloat(sharesInput);
    const amt = parseFloat(amountInput);
    return currentPrice > 0 ? amt / currentPrice : NaN;
  }, [mode, sharesInput, amountInput, currentPrice]);

  const validation = useMemo<string | null>(() => {
    if (!Number.isFinite(qty) || qty <= 0) return "Enter a quantity greater than 0.";
    if (qty > shares + 1e-9) return `Exceeds held shares (${fmtShares(shares)}).`;
    return null;
  }, [qty, shares]);

  function setFull() {
    setMode("shares");
    setSharesInput(String(shares));
  }

  async function handleConfirm() {
    if (validation) { setError(validation); return; }
    setSubmitting(true);
    setError(null);
    const sellQty = Number(qty.toFixed(6));
    try {
      const res = await closePosition(ticker, sellQty);
      if (res.success) {
        onSuccess(fmtShares(sellQty));
      } else {
        setError(res.error ?? "Order failed.");
        setSubmitting(false);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Order failed.");
      setSubmitting(false);
    }
  }

  const estValue = Number.isFinite(qty) ? qty * currentPrice : 0;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm"
      onClick={() => { if (!submitting) onClose(); }}
    >
      <div
        className="w-full max-w-sm bg-zinc-900 border border-zinc-800 rounded-xl p-5 space-y-4 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-baseline justify-between">
          <h3 className="text-base font-semibold text-white tracking-tight">Close {ticker}</h3>
          <span className="text-xs tabular-nums text-zinc-400">{fmtUSD(currentPrice)}</span>
        </div>

        {/* Mode toggle */}
        <div className="inline-flex w-full rounded-lg bg-zinc-800/50 p-0.5">
          {(["shares", "amount"] as Mode[]).map((m) => (
            <button
              key={m}
              onClick={() => setMode(m)}
              className={`flex-1 rounded-md px-2.5 py-1.5 text-xs font-medium transition-colors duration-150 ${
                mode === m ? "bg-zinc-100 text-zinc-900" : "text-zinc-400 hover:text-zinc-200"
              }`}
            >
              {m === "shares" ? "Shares" : "Amount ($)"}
            </button>
          ))}
        </div>

        {/* Input + live estimate */}
        {mode === "shares" ? (
          <div className="space-y-1.5">
            <label className="text-[10px] uppercase tracking-wider text-zinc-500">Shares to sell</label>
            <div className="flex items-center gap-3">
              <input
                type="number"
                min="0"
                step="any"
                value={sharesInput}
                onChange={(e) => { setSharesInput(e.target.value); setError(null); }}
                className="flex-1 bg-zinc-950 border border-zinc-700 rounded-lg px-3 py-2 text-sm tabular-nums text-zinc-100 focus:outline-none focus:border-zinc-500"
              />
              <span className="text-xs tabular-nums text-zinc-400 whitespace-nowrap">
                ≈ {fmtUSD(estValue)}
              </span>
            </div>
          </div>
        ) : (
          <div className="space-y-1.5">
            <label className="text-[10px] uppercase tracking-wider text-zinc-500">Amount ($)</label>
            <div className="flex items-center gap-3">
              <input
                type="number"
                min="0"
                step="any"
                value={amountInput}
                onChange={(e) => { setAmountInput(e.target.value); setError(null); }}
                className="flex-1 bg-zinc-950 border border-zinc-700 rounded-lg px-3 py-2 text-sm tabular-nums text-zinc-100 focus:outline-none focus:border-zinc-500"
              />
              <span className="text-xs tabular-nums text-zinc-400 whitespace-nowrap">
                ≈ {fmtShares(Number.isFinite(qty) ? qty : 0)} sh
              </span>
            </div>
          </div>
        )}

        {/* Close All shortcut */}
        <button
          onClick={setFull}
          className="text-[11px] font-medium text-zinc-400 hover:text-zinc-200 underline underline-offset-2 transition-colors duration-150"
        >
          Close All ({fmtShares(shares)} shares)
        </button>

        {(error ?? validation) && <p className="text-xs text-red-400">{error ?? validation}</p>}

        {/* Actions */}
        <div className="flex gap-2 pt-1">
          <button
            onClick={onClose}
            disabled={submitting}
            className="flex-1 rounded-lg border border-zinc-700 px-3 py-2 text-xs font-medium text-zinc-300 hover:bg-zinc-800 disabled:opacity-50 transition-colors duration-150"
          >
            Cancel
          </button>
          <button
            onClick={handleConfirm}
            disabled={submitting || validation !== null}
            className="flex-[2] rounded-lg bg-red-500 px-3 py-2 text-xs font-semibold text-white hover:bg-red-400 disabled:opacity-40 disabled:cursor-not-allowed transition-colors duration-150"
          >
            {submitting ? "Placing order…" : `Sell ${fmtShares(Number.isFinite(qty) ? qty : 0)} shares of ${ticker}`}
          </button>
        </div>
      </div>
    </div>
  );
}
