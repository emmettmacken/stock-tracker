"use client";
import { useEffect, useState, useCallback } from "react";
import { PaperAccount } from "@/lib/types";
import { fetchPaperAccount } from "@/lib/api";
import { StatCard } from "@/components/v4/portfolio/StatCard";
import { EquityCurve } from "@/components/v4/portfolio/EquityCurve";
import { PositionsPanel } from "@/components/v4/portfolio/PositionsPanel";
import { EdgeStats } from "@/components/v4/portfolio/EdgeStats";
import { PortfolioSettingsModal } from "@/components/v4/portfolio/PortfolioSettingsModal";
import { SectorExposurePanel } from "@/components/v3/portfolio/SectorExposure";
import { Skeleton } from "@/components/v3/Skeleton";
import { useLocalStorageBool } from "@/lib/useLocalStorageBool";
import { fmtUSD, fmtUSDSigned, fmtPctSigned } from "@/lib/format";

// Fixed paper-trading starting balance — Total Return is measured against this.
const STARTING_BALANCE = 100_000;

// liveEquity (when present) comes from the equity curve's 10s live-tip poll and overrides
// the account endpoint's equity for the Account Value / Total Return cards. Cash stays on
// the account endpoint's own poll — it only changes when a trade fires.
function AccountSummaryBar({ liveEquity }: { liveEquity: number | null }) {
  const [account, setAccount] = useState<PaperAccount | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(() => {
    fetchPaperAccount()
      .then(setAccount)
      .catch(() => setAccount({ available: false, error: "Failed to load account" }))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    load();
    const id = setInterval(load, 60_000);
    return () => clearInterval(id);
  }, [load]);

  if (loading && !account) {
    return (
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
        {[0, 1, 2].map((i) => <Skeleton key={i} className="h-[68px] w-full rounded-xl" />)}
      </div>
    );
  }

  if (!account?.available) {
    return (
      <div className="bg-zinc-900 border border-zinc-800 rounded-xl px-4 py-3 text-xs text-zinc-500">
        Alpaca paper account not connected —{" "}
        <span className="text-zinc-400">{account?.error ?? "add ALPACA_API_KEY + ALPACA_SECRET_KEY to your backend .env"}</span>
      </div>
    );
  }

  const equity = liveEquity ?? account.equity ?? 0;
  const totalReturn = equity - STARTING_BALANCE;
  const totalReturnPct = (totalReturn / STARTING_BALANCE) * 100;
  const returnPos = totalReturn >= 0;

  return (
    <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
      <StatCard label="Account Value" value={fmtUSD(equity)} />
      <StatCard label="Uninvested Cash" value={fmtUSD(account.cash ?? 0)} />
      <StatCard
        label="Total Return"
        value={`${fmtUSDSigned(totalReturn)} / ${fmtPctSigned(totalReturnPct)}`}
        valueClass={returnPos ? "text-emerald-400" : "text-red-400"}
      />
    </div>
  );
}

export default function PortfolioPage() {
  // Lifted from EquityCurve's 10s live-tip poll so the stat cards and the curve share one
  // fetch cycle. null until the first tip arrives (off-hours it stays null → cards fall back
  // to the account endpoint's equity).
  const [liveEquity, setLiveEquity] = useState<number | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  // Frontend-only preference: dotted reference line on the equity curve at $100k.
  const [showNetDeposits, setShowNetDeposits] = useLocalStorageBool("portfolio.showNetDeposits", false);

  return (
    <div className="min-h-screen bg-zinc-950 text-zinc-100">
      <div className="max-w-5xl mx-auto px-4 py-8 sm:py-10 space-y-8">
        <header className="flex items-start justify-between gap-4">
          <div>
            <h1 className="text-2xl sm:text-[28px] font-semibold tracking-tight text-white text-balance">Portfolio</h1>
            <p className="mt-1.5 text-zinc-400 text-sm leading-relaxed">
              Live paper-trading account · equity curve and open positions from Alpaca
            </p>
          </div>
          <button
            onClick={() => setSettingsOpen(true)}
            aria-label="Portfolio settings"
            title="Portfolio settings"
            className="mt-1 shrink-0 rounded-lg border border-zinc-800 p-2 text-zinc-400 hover:text-zinc-100 hover:bg-zinc-800/60 transition-colors duration-150 ease-out-quart"
          >
            <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth={1.6}>
              <circle cx="12" cy="12" r="3" />
              <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </button>
        </header>

        <section>
          <AccountSummaryBar liveEquity={liveEquity} />
        </section>

        <section>
          <EquityCurve onLiveEquity={setLiveEquity} showNetDeposits={showNetDeposits} netDepositsLevel={STARTING_BALANCE} />
        </section>

        <section>
          <PositionsPanel />
        </section>

        <section>
          <div className="bg-zinc-900 border border-zinc-800 rounded-xl p-4 sm:p-5 space-y-4">
            <div>
              <h2 className="text-sm font-semibold text-zinc-100 tracking-tight">Sector Exposure</h2>
              <p className="text-xs text-zinc-500 mt-1 leading-relaxed">
                Current allocation across sectors from your live open positions.
              </p>
            </div>
            <SectorExposurePanel />
          </div>
        </section>

        <section>
          <EdgeStats />
        </section>
      </div>

      {settingsOpen && (
        <PortfolioSettingsModal
          onClose={() => setSettingsOpen(false)}
          showNetDeposits={showNetDeposits}
          onShowNetDepositsChange={setShowNetDeposits}
        />
      )}
    </div>
  );
}
