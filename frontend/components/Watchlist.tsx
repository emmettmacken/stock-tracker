"use client";
import { useEffect, useState, useCallback, useRef } from "react";
import { SnapshotData } from "@/lib/types";
import {
  fetchWatchlistSnapshot, fetchFactors, addTickerDB, removeTickerDB,
} from "@/lib/api";
import { loadWatchlist, saveWatchlist } from "@/lib/watchlist";
import { TickerCard } from "./TickerCard";
import { AddTickerForm } from "./AddTickerForm";

function placeholder(ticker: string): SnapshotData {
  return {
    ticker,
    composite_score: null,
    signal: null,
    hmm_regime: null,
    price: null,
    price_change_pct: null,
    computed_at: null,
    factors: null,
  };
}

export function Watchlist() {
  const [order, setOrder] = useState<string[]>([]);
  const [snapshots, setSnapshots] = useState<Record<string, SnapshotData>>({});
  const [refreshing, setRefreshing] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const applySnapshots = useCallback((list: SnapshotData[]) => {
    setSnapshots((prev) => {
      const next = { ...prev };
      list.forEach((s) => { next[s.ticker] = s; });
      return next;
    });
  }, []);

  // Poll the cached snapshot endpoint while any ticker is still "Calculating…"
  // (e.g. a freshly added ticker whose background compute hasn't landed yet).
  const ensurePolling = useCallback(() => {
    if (pollRef.current) return;
    let ticks = 0;
    pollRef.current = setInterval(async () => {
      ticks += 1;
      try {
        const list = await fetchWatchlistSnapshot();
        applySnapshots(list);
        const stillCalculating = list.some((s) => s.computed_at === null);
        if (!stillCalculating || ticks >= 10) {
          if (pollRef.current) clearInterval(pollRef.current);
          pollRef.current = null;
        }
      } catch {
        /* transient — keep trying until the tick cap */
        if (ticks >= 10 && pollRef.current) {
          clearInterval(pollRef.current);
          pollRef.current = null;
        }
      }
    }, 4000);
  }, [applySnapshots]);

  useEffect(() => {
    // Fast first paint from localStorage, rendered as "Calculating…" placeholders.
    const local = loadWatchlist();
    if (local.length > 0) {
      setOrder(local);
      setSnapshots(Object.fromEntries(local.map((t) => [t, placeholder(t)])));
    }
    // Source of truth: one fast cached read, no live computation.
    fetchWatchlistSnapshot()
      .then((list) => {
        if (list.length > 0) {
          const tickers = list.map((s) => s.ticker);
          setOrder(tickers);
          setSnapshots(Object.fromEntries(list.map((s) => [s.ticker, s])));
          saveWatchlist(tickers);
          if (list.some((s) => s.computed_at === null)) ensurePolling();
        } else if (local.length > 0) {
          // Seed backend with existing localStorage tickers (triggers their compute).
          local.forEach((t) => addTickerDB(t).catch(() => {}));
          ensurePolling();
        }
      })
      .catch(() => { /* backend unavailable — placeholders already shown */ });

    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, [ensurePolling]);

  function addTicker(ticker: string) {
    const updated = [...order, ticker];
    setOrder(updated);
    setSnapshots((prev) => ({ ...prev, [ticker]: placeholder(ticker) }));
    saveWatchlist(updated);
    addTickerDB(ticker).catch(() => {});
    ensurePolling();
  }

  function removeTicker(ticker: string) {
    const updated = order.filter((t) => t !== ticker);
    setOrder(updated);
    saveWatchlist(updated);
    removeTickerDB(ticker).catch(() => {});
    setSnapshots((prev) => {
      const next = { ...prev };
      delete next[ticker];
      return next;
    });
  }

  // "Give me fresh numbers right now": explicit live recompute of every ticker via the
  // /api/factors/{ticker} path (which also updates the backend snapshot), then re-read
  // the consolidated cached snapshots for display.
  async function refreshAll() {
    setRefreshing(true);
    try {
      await Promise.all(order.map((t) => fetchFactors(t).catch(() => null)));
      const list = await fetchWatchlistSnapshot();
      applySnapshots(list);
    } finally {
      setRefreshing(false);
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-zinc-400 uppercase tracking-widest">
          Watchlist
        </h2>
        <button
          onClick={refreshAll}
          disabled={refreshing || order.length === 0}
          className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-lg
            bg-zinc-800 text-zinc-300 hover:bg-zinc-700 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          title="Recompute every ticker live (slower)"
        >
          <span className={refreshing ? "animate-spin inline-block" : ""}>⟳</span>
          {refreshing ? "Refreshing…" : "Refresh all"}
        </button>
      </div>

      <AddTickerForm onAdd={addTicker} existing={order} />

      {order.length === 0 && (
        <p className="text-sm text-zinc-500 text-center py-8">
          Add a ticker above to get started.
        </p>
      )}

      <div className="grid gap-3 sm:grid-cols-1 md:grid-cols-2 xl:grid-cols-3">
        {order.map((ticker) => (
          <TickerCard
            key={ticker}
            snapshot={snapshots[ticker] ?? placeholder(ticker)}
            onRemove={() => removeTicker(ticker)}
          />
        ))}
      </div>
    </div>
  );
}
