"use client";
import { useEffect, useState, useCallback } from "react";
import { SignalData } from "@/lib/types";
import { fetchSignal, fetchWatchlistDB, addTickerDB, removeTickerDB } from "@/lib/api";
import { loadWatchlist, saveWatchlist } from "@/lib/watchlist";
import { TickerCard } from "./TickerCard";
import { AddTickerForm } from "./AddTickerForm";

interface TickerState {
  data: SignalData | null;
  loading: boolean;
  error?: string;
}

const EMPTY: SignalData = {
  ticker: "",
  price: 0,
  prev_close: 0,
  change_pct: 0,
  signal: "HOLD",
  confidence: 0,
  current_state: "Flat-Mid Vol",
  current_return_bucket: 2,
  current_vol_bucket: 1,
  bullish_edge: 0,
  bearish_edge: 0,
  bull_edge_ci_low: 0,
  bull_edge_ci_high: 0,
  bear_edge_ci_low: 0,
  bear_edge_ci_high: 0,
  n_obs_current_state: 0,
  regime: "bull",
  high_confidence: false,
  transition_matrix_5x5: Array(5).fill(Array(5).fill(0.2)),
  bullish_heatmap: Array(5).fill(Array(3).fill(0.4)),
  row_observations: Array(5).fill(Array(3).fill(0)),
  stationary_distribution: Array(5).fill(0.2),
  return_labels: ["Strong Down", "Down", "Flat", "Up", "Strong Up"],
  vol_labels: ["Low Vol", "Mid Vol", "High Vol"],
  num_returns: 0,
  regime_window_size: 0,
};

export function Watchlist() {
  const [tickers, setTickers] = useState<string[]>([]);
  const [states, setStates] = useState<Record<string, TickerState>>({});
  const [refreshing, setRefreshing] = useState(false);

  useEffect(() => {
    // Show localStorage immediately for fast first paint
    const local = loadWatchlist();
    setTickers(local);
    // Then fetch from backend and reconcile (backend is source of truth)
    fetchWatchlistDB()
      .then((items) => {
        if (items.length > 0) {
          const dbList = items.map((i) => i.ticker);
          setTickers(dbList);
          saveWatchlist(dbList);
        } else if (local.length > 0) {
          // Seed backend with existing localStorage tickers
          local.forEach((t) => addTickerDB(t).catch(() => {}));
        }
      })
      .catch(() => { /* backend unavailable — localStorage already set */ });
  }, []);

  const loadTicker = useCallback(async (ticker: string) => {
    setStates((prev) => ({
      ...prev,
      [ticker]: { data: prev[ticker]?.data ?? null, loading: true },
    }));
    try {
      const data = await fetchSignal(ticker);
      setStates((prev) => ({ ...prev, [ticker]: { data, loading: false } }));
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "Unknown error";
      setStates((prev) => ({
        ...prev,
        [ticker]: { data: prev[ticker]?.data ?? null, loading: false, error: msg },
      }));
    }
  }, []);

  useEffect(() => {
    tickers.forEach((t) => {
      if (!states[t]) loadTicker(t);
    });
  }, [tickers, loadTicker]); // eslint-disable-line react-hooks/exhaustive-deps

  function addTicker(ticker: string) {
    const updated = [...tickers, ticker];
    setTickers(updated);
    saveWatchlist(updated);
    addTickerDB(ticker).catch(() => {});
  }

  function removeTicker(ticker: string) {
    const updated = tickers.filter((t) => t !== ticker);
    setTickers(updated);
    saveWatchlist(updated);
    removeTickerDB(ticker).catch(() => {});
    setStates((prev) => {
      const next = { ...prev };
      delete next[ticker];
      return next;
    });
  }

  async function refreshAll() {
    setRefreshing(true);
    await Promise.all(tickers.map(loadTicker));
    setRefreshing(false);
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-zinc-400 uppercase tracking-widest">
          Watchlist
        </h2>
        <button
          onClick={refreshAll}
          disabled={refreshing || tickers.length === 0}
          className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-lg
            bg-zinc-800 text-zinc-300 hover:bg-zinc-700 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
        >
          <span className={refreshing ? "animate-spin inline-block" : ""}>⟳</span>
          {refreshing ? "Refreshing…" : "Refresh all"}
        </button>
      </div>

      <AddTickerForm onAdd={addTicker} existing={tickers} />

      {tickers.length === 0 && (
        <p className="text-sm text-zinc-500 text-center py-8">
          Add a ticker above to get started.
        </p>
      )}

      <div className="grid gap-3 sm:grid-cols-1 md:grid-cols-2 xl:grid-cols-3">
        {tickers.map((ticker) => {
          const s = states[ticker];
          return (
            <TickerCard
              key={ticker}
              data={s?.data ?? { ...EMPTY, ticker }}
              loading={!s || s.loading}
              error={s?.error}
              onRemove={() => removeTicker(ticker)}
            />
          );
        })}
      </div>
    </div>
  );
}
