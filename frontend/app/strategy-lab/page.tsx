"use client";
import { useState, useEffect, useMemo } from "react";
import { FactorScoreData, SizingResult } from "@/lib/types";
import { fetchWatchlistSnapshot, fetchPortfolioSizing } from "@/lib/api";
import { loadWatchlist } from "@/lib/watchlist";
import { AllocationTable } from "@/components/v3/portfolio/AllocationTable";
import { PortfolioBacktestPanel } from "@/components/v3/portfolio/PortfolioBacktestPanel";
import { scoreTextColor } from "@/components/v3/FactorScorePill";
import { Skeleton } from "@/components/v3/Skeleton";
import { UpcomingEarnings } from "@/components/v4/UpcomingEarnings";

export default function PortfolioPage() {
  const [watchlist, setWatchlist] = useState<string[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [capital, setCapital] = useState(10000);
  const [method, setMethod] = useState<"kelly" | "vol">("vol");

  const [factorData, setFactorData] = useState<Record<string, FactorScoreData>>({});
  const [factorsLoading, setFactorsLoading] = useState(true);

  const [sizing, setSizing] = useState<SizingResult | null>(null);
  const [sizingLoading, setSizingLoading] = useState(false);
  const [sizingError, setSizingError] = useState<string | null>(null);

  // Load watchlist on mount
  useEffect(() => {
    const list = loadWatchlist();
    setWatchlist(list);
    setSelected(list);
  }, []);

  // Load factor scores for the whole watchlist in one fast snapshot read instead of
  // 28 live /api/factors recomputes. The snapshot's `factors` field is the same full
  // FactorScoreData payload /api/factors returns, so downstream consumers are unchanged.
  useEffect(() => {
    setFactorsLoading(true);
    fetchWatchlistSnapshot()
      .then((snapshots) => {
        const map: Record<string, FactorScoreData> = {};
        snapshots.forEach((s) => {
          if (s.factors) map[s.ticker] = s.factors;
        });
        setFactorData(map);
      })
      .catch(() => {})
      .finally(() => setFactorsLoading(false));
  }, []);

  const selectedKey = useMemo(() => [...selected].sort().join(","), [selected]);
  const allReady = useMemo(
    () => selected.length > 0 && selected.every((t) => !!factorData[t]),
    [selected, factorData]
  );

  // Recompute sizing when selection or capital changes and all factors are ready
  useEffect(() => {
    if (!allReady) return;
    const signals: Record<string, { composite_score: number }> = {};
    selected.forEach((t) => {
      if (factorData[t]) {
        signals[t] = {
          composite_score: factorData[t].composite_score,
        };
      }
    });
    setSizingLoading(true);
    setSizingError(null);
    fetchPortfolioSizing({ capital, tickers: selected, signals })
      .then(setSizing)
      .catch((e) => setSizingError(e instanceof Error ? e.message : "Sizing failed"))
      .finally(() => setSizingLoading(false));
  }, [allReady, selectedKey, capital]); // eslint-disable-line react-hooks/exhaustive-deps

  function toggleTicker(ticker: string) {
    setSelected((prev) =>
      prev.includes(ticker) ? prev.filter((t) => t !== ticker) : [...prev, ticker]
    );
  }

  const loadingCount = factorsLoading ? selected.filter((t) => !factorData[t]).length : 0;

  return (
    <div className="min-h-screen bg-zinc-950 text-zinc-100">
      <div className="max-w-5xl mx-auto px-4 py-8 sm:py-10">
        <header className="mb-8">
          <h1 className="text-2xl sm:text-[28px] font-semibold tracking-tight text-white text-balance">Strategy Lab</h1>
          <p className="mt-1.5 text-zinc-400 text-sm leading-relaxed">
            Size positions and backtest multi-ticker portfolios using composite factor signals.
          </p>
        </header>

        <div className="mb-8">
          <UpcomingEarnings />
        </div>

        <div className="flex gap-6 items-start">
          {/* Sidebar */}
          <aside className="w-44 shrink-0">
            <h2 className="text-[10px] font-semibold text-zinc-500 uppercase tracking-widest mb-3">
              Include in analysis
            </h2>
            {watchlist.length === 0 && (
              <p className="text-zinc-600 text-xs">Add tickers to your watchlist first.</p>
            )}
            <div className="space-y-2">
              {watchlist.map((ticker) => {
                const fd = factorData[ticker];
                const isLoading = factorsLoading && !fd;
                return (
                  <label
                    key={ticker}
                    className="flex items-center gap-2 cursor-pointer group"
                  >
                    <input
                      type="checkbox"
                      checked={selected.includes(ticker)}
                      onChange={() => toggleTicker(ticker)}
                      className="rounded accent-emerald-500 w-3.5 h-3.5 shrink-0"
                    />
                    <span className="text-sm text-zinc-300 group-hover:text-white transition-colors duration-150 ease-out-quart flex-1 min-w-0 truncate">
                      {ticker}
                    </span>
                    {isLoading ? (
                      <span className="inline-block w-3 h-3 border border-zinc-600 border-t-zinc-400 rounded-full animate-spin shrink-0" />
                    ) : fd ? (
                      <span className={`text-xs font-bold tabular-nums shrink-0 ${scoreTextColor(fd.composite_score)}`}>
                        {Math.round(fd.composite_score)}
                      </span>
                    ) : null}
                  </label>
                );
              })}
            </div>

            {/* Capital input */}
            <div className="mt-6">
              <label className="block text-[10px] font-semibold text-zinc-500 uppercase tracking-widest mb-2">
                Capital
              </label>
              <div className="relative">
                <span className="absolute left-2.5 top-1/2 -translate-y-1/2 text-zinc-500 text-sm">$</span>
                <input
                  type="number"
                  value={capital}
                  onChange={(e) => setCapital(Math.max(100, parseInt(e.target.value) || 10000))}
                  className="w-full bg-zinc-900 border border-zinc-800 rounded-lg pl-6 pr-2 py-1.5 text-sm text-zinc-200 tabular-nums
                    focus:outline-none focus:border-zinc-600 transition-colors duration-150 ease-out-quart"
                  min={100}
                  step={1000}
                />
              </div>
            </div>

            {/* Sizing method toggle */}
            <div className="mt-4">
              <label className="block text-[10px] font-semibold text-zinc-500 uppercase tracking-widest mb-2">
                Sizing Method
              </label>
              <div className="flex gap-0.5 rounded-lg border border-zinc-800 bg-zinc-900 p-0.5 text-xs">
                <button
                  onClick={() => setMethod("vol")}
                  className={`flex-1 rounded-md py-1.5 transition-colors duration-150 ease-out-quart ${
                    method === "vol" ? "bg-zinc-700 text-zinc-100 font-medium" : "text-zinc-500 hover:text-zinc-300"
                  }`}
                >
                  Vol-target
                </button>
                <button
                  onClick={() => setMethod("kelly")}
                  className={`flex-1 rounded-md py-1.5 transition-colors duration-150 ease-out-quart ${
                    method === "kelly" ? "bg-zinc-700 text-zinc-100 font-medium" : "text-zinc-500 hover:text-zinc-300"
                  }`}
                >
                  Kelly
                </button>
              </div>
            </div>
          </aside>

          {/* Main content */}
          <main className="flex-1 min-w-0 space-y-8">
            {/* Allocation section */}
            <section>
              <div className="flex items-center justify-between mb-3">
                <h2 className="text-sm font-semibold text-zinc-100 tracking-tight">
                  Position Sizing
                </h2>
                {loadingCount > 0 && (
                  <span className="text-zinc-500 text-xs flex items-center gap-1.5">
                    <span className="inline-block w-3 h-3 border border-zinc-600 border-t-zinc-400 rounded-full animate-spin" />
                    Loading {loadingCount} ticker{loadingCount !== 1 ? "s" : ""}…
                  </span>
                )}
              </div>

              {selected.length === 0 && (
                <p className="text-zinc-600 text-sm py-6 text-center">
                  Select at least one ticker from the sidebar.
                </p>
              )}

              {selected.length > 0 && !allReady && !sizingLoading && (
                <div className="space-y-2">
                  {[1, 2, 3].map((i) => (
                    <Skeleton key={i} className="h-10 w-full" />
                  ))}
                </div>
              )}

              {sizingLoading && (
                <div className="flex items-center gap-2 py-4 text-zinc-500 text-sm">
                  <span className="inline-block w-4 h-4 border-2 border-zinc-600 border-t-zinc-300 rounded-full animate-spin" />
                  Computing allocations…
                </div>
              )}

              {sizingError && (
                <div className="text-red-400 text-sm py-4">
                  <p className="mb-1">{sizingError}</p>
                  <button
                    onClick={() => {
                      setSizingError(null);
                      setSizing(null);
                    }}
                    className="text-zinc-500 hover:text-zinc-300 text-xs underline"
                  >
                    Retry
                  </button>
                </div>
              )}

              {sizing && !sizingLoading && !sizingError && (
                <>
                  <AllocationTable
                    sizing={sizing}
                    factorData={factorData}
                    method={method}
                    capital={capital}
                  />
                  <p className="text-zinc-600 text-[10px] mt-2">
                    {method === "kelly"
                      ? "Kelly: half-Kelly fraction capped at 25%. Unfavourable signals (score ≤ 50) get 0%."
                      : "Vol-targeted: weights are inversely proportional to 21-day realised volatility, normalised to sum to 1."}
                    {" "}Correlation penalty applies when ρ &gt; 0.7 with a higher-scored ticker.
                  </p>
                </>
              )}
            </section>

            {/* Backtest section */}
            <section className="border-t border-zinc-800 pt-6">
              <h2 className="text-sm font-semibold text-zinc-100 tracking-tight mb-4">Portfolio Backtest</h2>
              {selected.length === 0 ? (
                <p className="text-zinc-600 text-sm">Select tickers to run a backtest.</p>
              ) : (
                <PortfolioBacktestPanel tickers={selected} capital={capital} />
              )}
            </section>
          </main>
        </div>
      </div>
    </div>
  );
}
