"use client";
import { useState, useEffect, useCallback } from "react";
import { PaperAccount, PaperPosition, SignalLogEntry, TradeOutcome } from "@/lib/types";
import { AccountSummary } from "@/components/v4/AccountSummary";
import { PositionsTable } from "@/components/v4/PositionsTable";
import { SignalLogFeed } from "@/components/v4/SignalLogFeed";
import { ClosedTradesPanel } from "@/components/v4/ClosedTradesPanel";
import { AnalyticsTab } from "@/components/v4/AnalyticsTab";
import {
  triggerSignalJob,
  fetchPaperAccount, fetchPaperPositions,
  fetchSignalLog, fetchTradeHistory,
} from "@/lib/api";

const TABS = ["Overview", "Signal Log", "Closed Trades", "Analytics"] as const;
type Tab = (typeof TABS)[number];

function SectionHeader({ title, sub }: { title: string; sub?: string }) {
  return (
    <div className="mb-4">
      <h2 className="text-sm font-semibold text-zinc-100 tracking-tight">{title}</h2>
      {sub && <p className="text-xs text-zinc-500 mt-1 leading-relaxed">{sub}</p>}
    </div>
  );
}

function TabBar({ active, onChange }: { active: Tab; onChange: (t: Tab) => void }) {
  return (
    <div className="flex gap-1 border-b border-zinc-800 mb-6">
      {TABS.map((t) => (
        <button
          key={t}
          onClick={() => onChange(t)}
          aria-current={active === t ? "page" : undefined}
          className={`px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-[color,border-color] duration-150 ease-out-quart ${
            active === t
              ? "border-zinc-100 text-zinc-100"
              : "border-transparent text-zinc-500 hover:text-zinc-300 hover:border-zinc-700"
          }`}
        >
          {t}
        </button>
      ))}
    </div>
  );
}

export default function AutomationPage() {
  const [activeTab, setActiveTab] = useState<Tab>("Overview");
  const [running, setRunning] = useState(false);
  const [runMsg, setRunMsg] = useState<string | null>(null);

  // ── Shared data state (single 60s polling interval) ──────────────────────────
  const [account, setAccount] = useState<PaperAccount | null>(null);
  const [positionsData, setPositionsData] = useState<{
    available: boolean; positions?: PaperPosition[]; error?: string;
  } | null>(null);
  const [positionsLoading, setPositionsLoading] = useState(true);
  const [overviewUpdated, setOverviewUpdated] = useState<Date | null>(null);

  const [signalLog, setSignalLog] = useState<SignalLogEntry[] | null>(null);
  const [signalLogLoading, setSignalLogLoading] = useState(true);
  const [signalLogError, setSignalLogError] = useState<string | null>(null);

  const [trades, setTrades] = useState<TradeOutcome[] | null>(null);
  const [tradesLoading, setTradesLoading] = useState(true);
  const [tradesError, setTradesError] = useState<string | null>(null);

  // ── Fetch helpers ─────────────────────────────────────────────────────────────
  const loadOverview = useCallback(() => {
    fetchPaperAccount().then(setAccount).catch(() => {});
    setPositionsLoading(true);
    fetchPaperPositions()
      .then((d) => { setPositionsData(d); setOverviewUpdated(new Date()); })
      .catch((e) => setPositionsData({ available: false, error: e.message }))
      .finally(() => setPositionsLoading(false));
  }, []);

  const loadSignalLog = useCallback(() => {
    setSignalLogLoading(true);
    setSignalLogError(null);
    fetchSignalLog(50)
      .then(setSignalLog)
      .catch((e) => setSignalLogError(e.message))
      .finally(() => setSignalLogLoading(false));
  }, []);

  const loadTrades = useCallback(() => {
    setTradesLoading(true);
    setTradesError(null);
    fetchTradeHistory()
      .then(setTrades)
      .catch((e) => setTradesError(e.message))
      .finally(() => setTradesLoading(false));
  }, []);

  // Single 60s interval drives all polling
  useEffect(() => {
    loadOverview();
    loadSignalLog();
    loadTrades();
    const id = setInterval(loadOverview, 60_000);
    return () => clearInterval(id);
  }, [loadOverview, loadSignalLog, loadTrades]);

  // ── Run signals now ───────────────────────────────────────────────────────────
  async function handleRunNow() {
    setRunning(true);
    setRunMsg(null);
    try {
      const res = await triggerSignalJob();
      setRunMsg(res.message);
      setTimeout(loadSignalLog, 3000);
    } catch (e) {
      setRunMsg(e instanceof Error ? e.message : "Failed to start job");
    } finally {
      setRunning(false);
    }
  }

  return (
    <div className="min-h-screen bg-zinc-950 text-zinc-100">
      <div className="max-w-5xl mx-auto px-4 py-8 sm:py-10 space-y-6">
        <header className="flex items-start justify-between gap-4">
          <div>
            <h1 className="text-2xl sm:text-[28px] font-semibold tracking-tight text-white text-balance">Automation</h1>
            <p className="mt-1.5 text-zinc-400 text-sm leading-relaxed">
              Paper trading runs daily at 15:45 ET · stop-loss check at 09:35 ET
            </p>
          </div>
          <div className="flex flex-col items-end gap-1.5">
            <button
              onClick={handleRunNow}
              disabled={running}
              className="px-4 py-2 bg-zinc-100 hover:bg-white disabled:opacity-50 disabled:cursor-not-allowed text-zinc-900 text-sm font-medium rounded-lg whitespace-nowrap
                transition-[background-color,transform] duration-150 ease-out-quart active:scale-[0.98]"
            >
              {running ? (
                <span className="flex items-center gap-2">
                  <span className="inline-block w-3.5 h-3.5 border-2 border-zinc-400 border-t-zinc-900 rounded-full animate-spin" />
                  Running…
                </span>
              ) : "Run signals now"}
            </button>
            {runMsg && (
              <p className="text-[10px] text-zinc-500 max-w-xs text-right">{runMsg}</p>
            )}
          </div>
        </header>

        <TabBar active={activeTab} onChange={setActiveTab} />

        {activeTab === "Overview" && (
          <div className="space-y-8">
            <section>
              <AccountSummary data={account} lastUpdated={overviewUpdated} />
            </section>
            <section>
              <SectionHeader
                title="Open Positions"
                sub="ATR stop = entry − 1.5 × 21d ATR at signal. Positions auto-close after 21 trading days."
              />
              <PositionsTable
                data={positionsData}
                loading={positionsLoading}
                lastUpdated={overviewUpdated}
              />
            </section>
          </div>
        )}

        {activeTab === "Signal Log" && (
          <section>
            <SectionHeader
              title="Signal Log"
              sub="Last 50 decisions — what the system did and why it skipped"
            />
            <SignalLogFeed
              entries={signalLog}
              loading={signalLogLoading}
              error={signalLogError}
              onRetry={loadSignalLog}
            />
          </section>
        )}

        {activeTab === "Closed Trades" && (
          <section>
            <SectionHeader
              title="Closed Trades"
              sub="Completed paper trades with aggregate performance stats"
            />
            <ClosedTradesPanel
              trades={trades}
              loading={tradesLoading}
              error={tradesError}
              onRetry={loadTrades}
            />
          </section>
        )}

        {activeTab === "Analytics" && (
          <section>
            <AnalyticsTab />
          </section>
        )}
      </div>
    </div>
  );
}
