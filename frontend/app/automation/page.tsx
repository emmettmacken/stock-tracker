"use client";
import { useState } from "react";
import { AccountSummary } from "@/components/v4/AccountSummary";
import { PositionsTable } from "@/components/v4/PositionsTable";
import { SignalLogFeed } from "@/components/v4/SignalLogFeed";
import { ClosedTradesPanel } from "@/components/v4/ClosedTradesPanel";
import { triggerSignalJob } from "@/lib/api";

function SectionHeader({ title, sub }: { title: string; sub?: string }) {
  return (
    <div className="mb-4">
      <h2 className="text-sm font-semibold text-zinc-300">{title}</h2>
      {sub && <p className="text-xs text-zinc-600 mt-0.5">{sub}</p>}
    </div>
  );
}

export default function AutomationPage() {
  const [running, setRunning] = useState(false);
  const [runMsg, setRunMsg] = useState<string | null>(null);
  const [logRefresh, setLogRefresh] = useState(0);

  async function handleRunNow() {
    setRunning(true);
    setRunMsg(null);
    try {
      const res = await triggerSignalJob();
      setRunMsg(res.message);
      // Give the background job 3 s to log initial entries, then refresh the feed
      setTimeout(() => setLogRefresh((n) => n + 1), 3000);
    } catch (e) {
      setRunMsg(e instanceof Error ? e.message : "Failed to start job");
    } finally {
      setRunning(false);
    }
  }

  return (
    <div className="min-h-screen bg-zinc-950 text-white">
      <div className="max-w-5xl mx-auto px-4 py-8 space-y-8">
        <header className="flex items-start justify-between gap-4">
          <div>
            <h1 className="text-2xl font-bold tracking-tight">Automation</h1>
            <p className="mt-1 text-zinc-400 text-sm">
              Paper trading runs daily at 15:45 ET · stop-loss check at 09:35 ET
            </p>
          </div>
          <div className="flex flex-col items-end gap-1.5">
            <button
              onClick={handleRunNow}
              disabled={running}
              className="px-4 py-2 bg-zinc-700 hover:bg-zinc-600 disabled:opacity-50 disabled:cursor-not-allowed text-white text-sm font-medium rounded-lg transition-colors whitespace-nowrap"
            >
              {running ? (
                <span className="flex items-center gap-2">
                  <span className="inline-block w-3.5 h-3.5 border-2 border-zinc-500 border-t-zinc-200 rounded-full animate-spin" />
                  Running…
                </span>
              ) : "Run signals now"}
            </button>
            {runMsg && (
              <p className="text-[10px] text-zinc-500 max-w-xs text-right">{runMsg}</p>
            )}
          </div>
        </header>

        {/* Account strip */}
        <section>
          <AccountSummary />
        </section>

        {/* Open positions */}
        <section>
          <SectionHeader
            title="Open Positions"
            sub="ATR stop = entry − 1.5 × 21d ATR at signal. Positions auto-close after 21 trading days."
          />
          <PositionsTable />
        </section>

        {/* Signal log + Closed trades side-by-side on wider screens */}
        <div className="grid gap-8 lg:grid-cols-2">
          <section>
            <SectionHeader
              title="Signal Log"
              sub="Last 50 decisions — what the system did and why it skipped"
            />
            <SignalLogFeed refreshTrigger={logRefresh} />
          </section>

          <section>
            <SectionHeader
              title="Closed Trades"
              sub="Completed paper trades with aggregate performance stats"
            />
            <ClosedTradesPanel />
          </section>
        </div>
      </div>
    </div>
  );
}
