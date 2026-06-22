"use client";
import { useEffect, useState, useCallback } from "react";
import { PaperAccount } from "@/lib/types";
import { fetchPaperAccount } from "@/lib/api";
import { StatCard } from "@/components/v4/portfolio/StatCard";
import { EquityCurve } from "@/components/v4/portfolio/EquityCurve";
import { PositionsPanel } from "@/components/v4/portfolio/PositionsPanel";
import { EdgeStats } from "@/components/v4/portfolio/EdgeStats";
import { SectorExposurePanel } from "@/components/v3/portfolio/SectorExposure";
import { Skeleton } from "@/components/v3/Skeleton";
import { fmtUSD, fmtUSDSigned, fmtPctSigned } from "@/lib/format";

// Fixed paper-trading starting balance — Total Return is measured against this.
const STARTING_BALANCE = 100_000;

function AccountSummaryBar() {
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

  const equity = account.equity ?? 0;
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
  return (
    <div className="min-h-screen bg-zinc-950 text-zinc-100">
      <div className="max-w-5xl mx-auto px-4 py-8 sm:py-10 space-y-8">
        <header>
          <h1 className="text-2xl sm:text-[28px] font-semibold tracking-tight text-white text-balance">Portfolio</h1>
          <p className="mt-1.5 text-zinc-400 text-sm leading-relaxed">
            Live paper-trading account · equity curve and open positions from Alpaca
          </p>
        </header>

        <section>
          <AccountSummaryBar />
        </section>

        <section>
          <EquityCurve />
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
    </div>
  );
}
