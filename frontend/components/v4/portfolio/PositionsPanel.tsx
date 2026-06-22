"use client";
import { useEffect, useMemo, useState, useCallback } from "react";
import Link from "next/link";
import { PaperPosition, EntrySignal } from "@/lib/types";
import { fetchPaperPositions, fetchEntrySignals, fetchFactors } from "@/lib/api";
import { scoreTextColor } from "@/components/v3/FactorScorePill";
import { Skeleton } from "@/components/v3/Skeleton";
import { StatCard } from "./StatCard";
import { ClosePositionModal } from "./ClosePositionModal";
import { fmtUSD, fmtUSDSigned, fmtPctSigned } from "@/lib/format";

// One enriched position row joining the Alpaca position, its signal_log entry data, and
// the freshly-fetched current composite score.
interface Row {
  ticker: string;
  entryDate: string | null;
  entryPrice: number;       // actual avg fill from Alpaca
  currentPrice: number;
  pnlAbs: number;           // (current − entry) × shares
  pnlPct: number;
  shares: number;
  value: number;
  entryScore: number | null;
  // undefined = still loading · null = unavailable ("—") · number = score
  currentScore: number | null | undefined;
}

type SortKey =
  | "ticker" | "entryDate" | "entryPrice" | "currentPrice"
  | "pnlAbs" | "pnlPct" | "shares" | "value" | "entryScore" | "currentScore";

const COLS: { key: SortKey; label: string; numeric: boolean }[] = [
  { key: "ticker",       label: "Ticker",        numeric: false },
  { key: "entryDate",    label: "Entry date",    numeric: false },
  { key: "entryPrice",   label: "Entry price",   numeric: true },
  { key: "currentPrice", label: "Current price", numeric: true },
  { key: "pnlAbs",       label: "P&L $",         numeric: true },
  { key: "pnlPct",       label: "P&L %",         numeric: true },
  { key: "shares",       label: "Shares",        numeric: true },
  { key: "value",        label: "Value",         numeric: true },
  { key: "entryScore",   label: "Entry score",   numeric: true },
  { key: "currentScore", label: "Current score", numeric: true },
];

function fmtEntryDate(iso: string | null): string {
  if (!iso) return "—";
  return iso.slice(0, 10);
}

function ScoreCell({ score }: { score: number | null | undefined }) {
  if (score === undefined) return <span className="text-zinc-700">·</span>;
  if (score === null) return <span className="text-zinc-700">—</span>;
  return <span className={`font-bold tabular-nums ${scoreTextColor(score)}`}>{score.toFixed(1)}</span>;
}

export function PositionsPanel() {
  const [positions, setPositions] = useState<PaperPosition[] | null>(null);
  const [entries, setEntries] = useState<Record<string, EntrySignal>>({});
  const [scores, setScores] = useState<Record<string, number | null>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [sortKey, setSortKey] = useState<SortKey>("pnlPct");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");

  // Position the Close modal is open for (null = closed), plus the success toast.
  const [closing, setClosing] = useState<Row | null>(null);
  const [toast, setToast] = useState<string | null>(null);

  const load = useCallback(() => {
    setError(null);
    // Positions (with entry data) — the table can't render without these.
    Promise.all([fetchPaperPositions(), fetchEntrySignals().catch(() => ({ available: false }))])
      .then(([pos, ent]) => {
        if (!pos.available) { setError(pos.error ?? "Positions unavailable"); setPositions([]); return; }
        setPositions(pos.positions ?? []);
        setEntries(("entries" in ent && ent.entries) ? ent.entries : {});
      })
      .catch((e) => setError(e instanceof Error ? e.message : "Failed to load positions"))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    load();
    const id = setInterval(load, 60_000);
    return () => clearInterval(id);
  }, [load]);

  // Fetch the current composite score per ticker independently — a failure for one ticker
  // shows "—" in its cell rather than erroring the whole table.
  useEffect(() => {
    if (!positions) return;
    let cancelled = false;
    for (const p of positions) {
      fetchFactors(p.ticker)
        .then((f) => { if (!cancelled) setScores((s) => ({ ...s, [p.ticker]: f.composite_score })); })
        .catch(() => { if (!cancelled) setScores((s) => ({ ...s, [p.ticker]: null })); });
    }
    return () => { cancelled = true; };
  }, [positions]);

  const rows = useMemo<Row[]>(() => {
    if (!positions) return [];
    return positions.map((p) => {
      const entry = entries[p.ticker];
      return {
        ticker:       p.ticker,
        entryDate:    entry?.entry_date ?? null,
        entryPrice:   p.entry_price,
        currentPrice: p.current_price,
        pnlAbs:       (p.current_price - p.entry_price) * p.qty,
        pnlPct:       p.pnl_pct,
        shares:       p.qty,
        value:        p.market_value,
        entryScore:   entry?.entry_score ?? p.composite_score ?? null,
        currentScore: p.ticker in scores ? scores[p.ticker] : undefined,
      };
    });
  }, [positions, entries, scores]);

  const totals = useMemo(() => {
    const count = rows.length;
    const value = rows.reduce((s, r) => s + r.value, 0);
    const pnlAbs = rows.reduce((s, r) => s + r.pnlAbs, 0);
    const cost = rows.reduce((s, r) => s + r.entryPrice * r.shares, 0);
    const pnlPct = cost ? (pnlAbs / cost) * 100 : 0;
    return { count, value, pnlAbs, pnlPct };
  }, [rows]);

  const sorted = useMemo(() => {
    const dir = sortDir === "desc" ? -1 : 1;
    return [...rows].sort((a, b) => {
      const av = a[sortKey];
      const bv = b[sortKey];
      // nulls/undefined sort last regardless of direction
      const an = av === null || av === undefined;
      const bn = bv === null || bv === undefined;
      if (an && bn) return 0;
      if (an) return 1;
      if (bn) return -1;
      if (typeof av === "string" && typeof bv === "string") return dir * av.localeCompare(bv);
      return dir * ((av as number) - (bv as number));
    });
  }, [rows, sortKey, sortDir]);

  // Auto-dismiss the success toast.
  useEffect(() => {
    if (!toast) return;
    const id = setTimeout(() => setToast(null), 4000);
    return () => clearTimeout(id);
  }, [toast]);

  // After a sell order is placed: toast, close the modal, and refresh positions in 2s
  // (giving Alpaca time to process the fill before we re-read).
  function handleCloseSuccess(qtyLabel: string) {
    const ticker = closing?.ticker ?? "";
    setToast(`Sell order placed for ${qtyLabel} shares of ${ticker}`);
    setClosing(null);
    setTimeout(load, 2000);
  }

  function handleSort(key: SortKey) {
    if (key === sortKey) setSortDir((d) => (d === "desc" ? "asc" : "desc"));
    else { setSortKey(key); setSortDir(key === "ticker" || key === "entryDate" ? "asc" : "desc"); }
  }

  return (
    <div className="space-y-4">
      {/* Summary stats */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        {loading && !positions ? (
          [0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-[68px] w-full rounded-xl" />)
        ) : (
          <>
            <StatCard label="Open Positions" value={totals.count} />
            <StatCard label="Market Value" value={fmtUSD(totals.value)} />
            <StatCard
              label="Unrealised P&L"
              value={fmtUSDSigned(totals.pnlAbs)}
              valueClass={totals.pnlAbs >= 0 ? "text-emerald-400" : "text-red-400"}
            />
            <StatCard
              label="Unrealised P&L %"
              value={fmtPctSigned(totals.pnlPct)}
              valueClass={totals.pnlPct >= 0 ? "text-emerald-400" : "text-red-400"}
            />
          </>
        )}
      </div>

      {/* Table */}
      <div className="bg-zinc-900 border border-zinc-800 rounded-xl p-4 sm:p-5">
        <h2 className="text-sm font-semibold text-zinc-100 tracking-tight mb-4">Open Positions</h2>

        {loading && !positions ? (
          <Skeleton className="h-32 w-full" />
        ) : error ? (
          <div className="text-red-400 text-xs py-4">
            {error}{" "}
            <button onClick={load} className="underline text-zinc-400 hover:text-white ml-1">Retry</button>
          </div>
        ) : rows.length === 0 ? (
          <div className="text-center py-8 text-zinc-600 text-sm">
            No open positions.
            <div className="text-[10px] mt-1 text-zinc-700">
              Positions will appear here once the signal job places orders.
            </div>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-zinc-800 text-left">
                  {COLS.map((c) => (
                    <th
                      key={c.key}
                      onClick={() => handleSort(c.key)}
                      className={`py-2 pr-4 text-zinc-500 font-medium cursor-pointer hover:text-zinc-300 select-none whitespace-nowrap transition-colors duration-150 ease-out-quart ${
                        c.numeric ? "text-right" : "text-left"
                      }`}
                    >
                      {c.label} {sortKey === c.key ? (sortDir === "desc" ? "↓" : "↑") : ""}
                    </th>
                  ))}
                  <th className="py-2 text-right text-zinc-500 font-medium select-none" />
                </tr>
              </thead>
              <tbody>
                {sorted.map((r) => {
                  const pnlPos = r.pnlAbs >= 0;
                  const pnlClass = pnlPos ? "text-emerald-400" : "text-red-400";
                  return (
                    <tr key={r.ticker} className="border-b border-zinc-800/50 hover:bg-zinc-800/30 transition-colors duration-150 ease-out-quart">
                      <td className="py-2.5 pr-4 font-bold">
                        <Link href={`/stock/${r.ticker}`} className="text-zinc-200 hover:text-white hover:underline">
                          {r.ticker}
                        </Link>
                      </td>
                      <td className="py-2.5 pr-4 tabular-nums text-zinc-400 whitespace-nowrap">{fmtEntryDate(r.entryDate)}</td>
                      <td className="py-2.5 pr-4 tabular-nums text-zinc-400 text-right">{fmtUSD(r.entryPrice)}</td>
                      <td className="py-2.5 pr-4 tabular-nums text-zinc-200 text-right">{fmtUSD(r.currentPrice)}</td>
                      <td className={`py-2.5 pr-4 tabular-nums font-semibold text-right ${pnlClass}`}>{fmtUSDSigned(r.pnlAbs)}</td>
                      <td className={`py-2.5 pr-4 tabular-nums font-semibold text-right ${pnlClass}`}>{fmtPctSigned(r.pnlPct)}</td>
                      <td className="py-2.5 pr-4 tabular-nums text-zinc-400 text-right">{r.shares}</td>
                      <td className="py-2.5 pr-4 tabular-nums text-zinc-300 text-right">{fmtUSD(r.value, { decimals: 0 })}</td>
                      <td className="py-2.5 pr-4 text-right"><ScoreCell score={r.entryScore} /></td>
                      <td className="py-2.5 pr-4 text-right"><ScoreCell score={r.currentScore} /></td>
                      <td className="py-2.5 text-right whitespace-nowrap">
                        <button
                          onClick={() => setClosing(r)}
                          className="rounded-md border border-zinc-700 px-2.5 py-1 text-[11px] font-medium text-zinc-300 hover:bg-zinc-800 hover:text-white transition-colors duration-150 ease-out-quart"
                        >
                          Close
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {closing && (
        <ClosePositionModal
          ticker={closing.ticker}
          currentPrice={closing.currentPrice}
          shares={closing.shares}
          onClose={() => setClosing(null)}
          onSuccess={handleCloseSuccess}
        />
      )}

      {toast && (
        <div className="fixed bottom-6 left-1/2 -translate-x-1/2 z-50 rounded-lg bg-emerald-500/95 px-4 py-2.5 text-xs font-medium text-white shadow-lg">
          {toast}
        </div>
      )}
    </div>
  );
}
