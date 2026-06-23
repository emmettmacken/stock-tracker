"use client";
import { useEffect, useState, useCallback, useRef, useMemo } from "react";
import { SnapshotData } from "@/lib/types";
import {
  fetchWatchlistSnapshot, fetchFactors, addTickerDB, removeTickerDB,
  fetchPaperPositions,
} from "@/lib/api";
import { loadWatchlist, saveWatchlist } from "@/lib/watchlist";
import { BUY_ZONE_THRESHOLD } from "@/lib/whyChip";
import { TickerCard } from "./TickerCard";
import { AddTickerForm } from "./AddTickerForm";

type FilterMode = "all" | "buy" | "held";
type SortMode = "score" | "az";

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
  const [held, setHeld] = useState<Set<string>>(new Set());
  const [filter, setFilter] = useState<FilterMode>("all");
  const [sectorFilter, setSectorFilter] = useState<string>("all");
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState<SortMode>("score");
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

  // Cross-reference live Alpaca positions to mark held vs watch-only cards.
  useEffect(() => {
    let cancelled = false;
    fetchPaperPositions()
      .then((d) => {
        if (cancelled) return;
        const set = d.available && d.positions
          ? new Set(d.positions.map((p) => p.ticker))
          : new Set<string>();
        setHeld(set);
      })
      .catch(() => { /* held state is optional */ });
    return () => { cancelled = true; };
  }, []);

  // Sectors present across the (already-fetched) snapshots — powers the sector filter.
  const sectors = useMemo(() => {
    const set = new Set<string>();
    order.forEach((t) => {
      const sec = snapshots[t]?.factors?.sector;
      if (sec && sec !== "Unknown") set.add(sec);
    });
    return [...set].sort();
  }, [order, snapshots]);

  // Client-side filtered + sorted view over the already-fetched snapshot data.
  const view = useMemo(() => {
    let list = order.map((t) => snapshots[t] ?? placeholder(t));
    if (filter === "buy") list = list.filter((s) => (s.composite_score ?? -1) >= BUY_ZONE_THRESHOLD);
    else if (filter === "held") list = list.filter((s) => held.has(s.ticker));
    if (sectorFilter !== "all") list = list.filter((s) => s.factors?.sector === sectorFilter);
    const q = query.trim().toLowerCase();
    if (q) {
      list = list.filter((s) =>
        s.ticker.toLowerCase().includes(q) ||
        (s.factors?.company_name?.toLowerCase().includes(q) ?? false)
      );
    }
    list = [...list].sort((a, b) =>
      sort === "score"
        ? (b.composite_score ?? -1) - (a.composite_score ?? -1)
        : a.ticker.localeCompare(b.ticker)
    );
    return list;
  }, [order, snapshots, filter, sectorFilter, sort, held, query]);

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
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        <h2 className="text-xs font-semibold text-zinc-500 uppercase tracking-[0.14em]">
          Watchlist
          {order.length > 0 && (
            <span className="ml-2 font-normal normal-case tracking-normal text-zinc-600">
              {order.length}
            </span>
          )}
        </h2>
        <button
          onClick={refreshAll}
          disabled={refreshing || order.length === 0}
          className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-lg
            bg-zinc-800 text-zinc-200 hover:bg-zinc-700 disabled:opacity-40 disabled:cursor-not-allowed
            transition-[background-color,transform] duration-150 ease-out-quart active:scale-[0.98]"
          title="Recompute every ticker live (slower)"
        >
          <svg
            width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor"
            strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round"
            className={refreshing ? "animate-spin" : ""}
            aria-hidden
          >
            <path d="M21 12a9 9 0 1 1-2.64-6.36" />
            <path d="M21 3v6h-6" />
          </svg>
          {refreshing ? "Refreshing…" : "Refresh all"}
        </button>
      </div>

      <AddTickerForm onAdd={addTicker} existing={order} />

      {order.length === 0 && (
        <div className="rounded-xl border border-dashed border-zinc-800 py-12 text-center">
          <p className="text-sm text-zinc-400">Your watchlist is empty</p>
          <p className="mt-1 text-xs text-zinc-600">Add a ticker above to get started.</p>
        </div>
      )}

      {order.length > 0 && (
        <input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search tickers..."
          aria-label="Search tickers"
          className="w-full rounded-lg bg-zinc-900 border border-zinc-800 px-3 py-2 text-sm text-zinc-200
            placeholder:text-zinc-600 focus:outline-none focus:border-zinc-600 transition-colors duration-150 ease-out-quart"
        />
      )}

      {order.length > 0 && (
        <div className="flex items-center justify-between gap-3 flex-wrap">
          <div className="inline-flex rounded-lg bg-zinc-800/50 p-0.5 text-xs">
            {([
              ["all", "All"],
              ["buy", "Buy zone"],
              ["held", "Held"],
            ] as const).map(([key, label]) => (
              <button
                key={key}
                onClick={() => setFilter(key)}
                className={`rounded-md px-2.5 py-1 font-medium transition-colors duration-150 ease-out-quart ${
                  filter === key ? "bg-zinc-100 text-zinc-900" : "text-zinc-400 hover:text-zinc-200"
                }`}
              >
                {label}
                {key === "held" && held.size > 0 && (
                  <span className="ml-1 text-[10px] opacity-70">{held.size}</span>
                )}
              </button>
            ))}
          </div>

          <div className="flex items-center gap-2">
            {sectors.length > 0 && (
              <select
                value={sectorFilter}
                onChange={(e) => setSectorFilter(e.target.value)}
                className="rounded-lg bg-zinc-900 border border-zinc-800 px-2 py-1.5 text-xs text-zinc-300
                  focus:outline-none focus:border-zinc-600 transition-colors duration-150 ease-out-quart"
                aria-label="Filter by sector"
              >
                <option value="all">All sectors</option>
                {sectors.map((sec) => (
                  <option key={sec} value={sec}>{sec}</option>
                ))}
              </select>
            )}
            <select
              value={sort}
              onChange={(e) => setSort(e.target.value as SortMode)}
              className="rounded-lg bg-zinc-900 border border-zinc-800 px-2 py-1.5 text-xs text-zinc-300
                focus:outline-none focus:border-zinc-600 transition-colors duration-150 ease-out-quart"
              aria-label="Sort"
            >
              <option value="score">Sort: Score</option>
              <option value="az">Sort: A–Z</option>
            </select>
          </div>
        </div>
      )}

      {order.length > 0 && view.length === 0 && (
        <p className="text-sm text-zinc-500 py-8 text-center">
          {query.trim()
            ? `No results for "${query.trim()}"`
            : "No tickers match this filter."}
        </p>
      )}

      <div className="grid gap-3 sm:grid-cols-1 md:grid-cols-2 xl:grid-cols-3">
        {view.map((snap) => (
          <TickerCard
            key={snap.ticker}
            snapshot={snap}
            held={held.has(snap.ticker)}
            onRemove={() => removeTicker(snap.ticker)}
          />
        ))}
      </div>
    </div>
  );
}
