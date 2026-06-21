"use client";
import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { SnapshotData, SignalData, DecisionTrail } from "@/lib/types";
import { fetchWatchlistSnapshot, refreshTicker, fetchSignal, fetchDecisionTrail } from "@/lib/api";
import { StockHeader } from "@/components/v3/stock/StockHeader";
import { VerdictBands } from "@/components/v3/stock/VerdictBands";
import { FactorBreakdown } from "@/components/v3/stock/FactorBreakdown";
import { MarkovDetail } from "@/components/v3/stock/MarkovDetail";
import { AltDataTab } from "@/components/v3/AltDataTab";
import { BacktestPanel } from "@/components/BacktestPanel";
import { EligibilityBanner, DecisionTrailList } from "@/components/v3/stock/DecisionTrail";
import { PositionBanner } from "@/components/v3/stock/PositionBanner";
import { PriceChart } from "@/components/v3/stock/PriceChart";
import { TickerAnalytics } from "@/components/v3/stock/TickerAnalytics";
import { Period, DEFAULT_PERIOD } from "@/lib/period";

function Section({
  title,
  sub,
  children,
}: {
  title: string;
  sub?: string;
  children: React.ReactNode;
}) {
  return (
    <section className="bg-zinc-900 border border-zinc-800 rounded-xl p-5">
      <div className="mb-4">
        <h2 className="text-sm font-semibold text-zinc-100 tracking-tight">{title}</h2>
        {sub && <p className="text-xs text-zinc-500 mt-1 leading-relaxed max-w-prose">{sub}</p>}
      </div>
      {children}
    </section>
  );
}

export default function StockDetailPage({ params }: { params: { ticker: string } }) {
  const ticker = params.ticker.toUpperCase();

  const [snapshot, setSnapshot] = useState<SnapshotData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const [signalData, setSignalData] = useState<SignalData | null>(null);
  const [signalLoading, setSignalLoading] = useState(true);
  const [signalError, setSignalError] = useState<string | null>(null);

  const [trail, setTrail] = useState<DecisionTrail | null>(null);

  // Selected time window — drives the price chart AND the per-ticker analytics below.
  const [period, setPeriod] = useState<Period>(DEFAULT_PERIOD);

  const loadSignal = useCallback(() => {
    setSignalLoading(true);
    setSignalError(null);
    fetchSignal(ticker)
      .then(setSignalData)
      .catch((e) => setSignalError(e instanceof Error ? e.message : "Failed to load"))
      .finally(() => setSignalLoading(false));
  }, [ticker]);

  // Fast load from the cached watchlist snapshot; fall back to a live compute
  // if this ticker isn't in the snapshot yet (e.g. just added).
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchWatchlistSnapshot()
      .then(async (list) => {
        const found = list.find((s) => s.ticker === ticker);
        if (found && found.factors) {
          if (!cancelled) setSnapshot(found);
        } else {
          const fresh = await refreshTicker(ticker);
          if (!cancelled) setSnapshot(fresh);
        }
      })
      .catch(async () => {
        try {
          const fresh = await refreshTicker(ticker);
          if (!cancelled) setSnapshot(fresh);
        } catch (e) {
          if (!cancelled) setError(e instanceof Error ? e.message : "Failed to load ticker");
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [ticker]);

  useEffect(() => {
    loadSignal();
  }, [loadSignal]);

  // Read-only decision trail (most recent gate-by-gate evaluation from signal_log).
  useEffect(() => {
    let cancelled = false;
    fetchDecisionTrail(ticker)
      .then((d) => { if (!cancelled) setTrail(d); })
      .catch(() => { if (!cancelled) setTrail(null); });
    return () => { cancelled = true; };
  }, [ticker]);

  async function handleRefresh() {
    setRefreshing(true);
    try {
      const fresh = await refreshTicker(ticker);
      setSnapshot(fresh);
      loadSignal();
    } catch {
      /* keep the stale snapshot in place */
    } finally {
      setRefreshing(false);
    }
  }

  return (
    <main className="min-h-screen bg-zinc-950 text-zinc-100">
      <div className="max-w-4xl mx-auto px-4 py-8 space-y-6">
        <Link
          href="/"
          className="group inline-flex items-center gap-1.5 text-sm text-zinc-500 hover:text-zinc-300 transition-colors duration-150 ease-out-quart"
        >
          <span className="transition-transform duration-150 ease-out-quart group-hover:-translate-x-0.5">←</span>
          Back to watchlist
        </Link>

        {loading ? (
          <div className="flex items-center justify-center py-24 gap-2.5 text-zinc-500 text-sm">
            <span className="inline-block w-5 h-5 border-2 border-zinc-800 border-t-zinc-400 rounded-full animate-spin" />
            Loading {ticker}…
          </div>
        ) : error || !snapshot ? (
          <div className="py-24 text-center">
            <p className="text-red-400 text-sm mb-3">{error ?? `No data for ${ticker}.`}</p>
            <Link href="/" className="text-zinc-400 hover:text-white text-sm underline underline-offset-2">
              Return to watchlist
            </Link>
          </div>
        ) : !snapshot.factors ? (
          <div className="py-24 text-center text-zinc-500 text-sm">
            Still computing factor scores for {ticker} — try refreshing in a moment.
          </div>
        ) : (
          <>
            <StockHeader snapshot={snapshot} refreshing={refreshing} onRefresh={handleRefresh} />

            <Section
              title="Price history"
              sub="Historical closing price. Pick a time period to scope the whole page; toggle moving averages. Entry/exit markers show where the system has traded this ticker."
            >
              <PriceChart ticker={ticker} period={period} onPeriodChange={setPeriod} />
            </Section>

            <PositionBanner ticker={ticker} />

            <EligibilityBanner trail={trail} />

            <VerdictBands score={snapshot.composite_score ?? 0} />

            <Section
              title="Decision trail"
              sub="How this ticker fared against each gate in the most recent signal-job run, in the order they're actually checked. The list stops at the first gate it failed."
            >
              <DecisionTrailList trail={trail} />
            </Section>

            <Section
              title="Analytics"
              sub="This system's realized track record on this ticker, scoped to the selected time period, with a buy-and-hold comparison over the same window."
            >
              <TickerAnalytics ticker={ticker} period={period} />
            </Section>

            <Section
              title="Factor breakdown"
              sub="The factors that combine into the composite score above, with each factor's weight and the raw numbers behind it. A factor marked 'excluded' had no data, and the remaining weights were renormalized to produce the score."
            >
              <FactorBreakdown data={snapshot.factors} />
            </Section>

            <Section
              title="Markov chain detail"
              sub="How this stock has historically behaved from its current price/volume state."
            >
              {signalLoading ? (
                <div className="flex items-center gap-2 py-8 justify-center text-zinc-500 text-xs">
                  <span className="inline-block w-4 h-4 border-2 border-zinc-600 border-t-zinc-300 rounded-full animate-spin" />
                  Loading signal analysis…
                </div>
              ) : signalError ? (
                <div className="py-4 text-center">
                  <p className="text-red-400 text-xs mb-2">{signalError}</p>
                  <button onClick={loadSignal} className="text-zinc-400 hover:text-white text-xs underline">
                    Retry
                  </button>
                </div>
              ) : signalData ? (
                <MarkovDetail data={signalData} />
              ) : null}
            </Section>

            <Section
              title="Alternative data"
              sub="Short interest — genuinely informational context that is not part of the composite score above."
            >
              <AltDataTab ticker={ticker} />
            </Section>

            <Section
              title="Backtest"
              sub="Walk-forward backtest of this ticker's signal over its price history."
            >
              <BacktestPanel ticker={ticker} />
            </Section>
          </>
        )}
      </div>
    </main>
  );
}
