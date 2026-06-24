"use client";
import { useState, useEffect } from "react";
import Link from "next/link";
import { UpcomingEarning } from "@/lib/types";
import { fetchUpcomingEarnings, fetchPaperPositions } from "@/lib/api";

const WINDOWS = [7, 14, 30, 60] as const;

// Composite score colour bands (mirror the rest of the app: ≥63 buy, 45–62 watch, <45 avoid).
function scoreColor(score: number | null): string {
  if (score == null) return "text-zinc-600";
  if (score >= 63) return "text-emerald-400";
  if (score >= 45) return "text-amber-400";
  return "text-red-400";
}

function surpriseColor(pct: number | null): string {
  if (pct == null) return "text-zinc-600";
  return pct >= 0 ? "text-emerald-400" : "text-red-400";
}

function fmtSurprise(pct: number | null): string {
  if (pct == null) return "—";
  return `${pct >= 0 ? "+" : "−"}${Math.abs(pct).toFixed(2)}%`;
}

// "YYYY-MM-DD" → "DD/MM/YYYY" (string split avoids any timezone shift).
function fmtDate(iso: string): string {
  const [y, m, d] = iso.split("-");
  return `${d}/${m}/${y}`;
}

function fmtDaysUntil(n: number): string {
  if (n === 0) return "Today";
  if (n === 1) return "Tomorrow";
  return `${n} days`;
}

export function UpcomingEarnings() {
  const [days, setDays] = useState<number>(30);
  const [earnings, setEarnings] = useState<UpcomingEarning[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [openPositions, setOpenPositions] = useState<Set<string>>(new Set());

  // Open positions drive the earnings-blackout warning badge. Fetched independently — a
  // failure here just means no badges, never an error on the earnings table itself — but
  // it's logged (not swallowed) so a silently-empty position set is diagnosable. Tickers
  // are uppercased so the badge lookup is case-insensitive against the earnings rows.
  useEffect(() => {
    fetchPaperPositions()
      .then((res) => {
        if (res.available && res.positions) {
          setOpenPositions(new Set(res.positions.map((p) => p.ticker.toUpperCase())));
        } else {
          console.warn("Upcoming earnings: open positions unavailable", res.error);
        }
      })
      .catch((e) => console.warn("Upcoming earnings: open positions fetch failed", e));
  }, []);

  useEffect(() => {
    setLoading(true);
    setError(null);
    fetchUpcomingEarnings(days)
      .then((data) => setEarnings(data.earnings))
      .catch((e) => setError(e instanceof Error ? e.message : "Failed to load earnings"))
      .finally(() => setLoading(false));
  }, [days]);

  return (
    <section className="border-b border-zinc-800 pb-8">
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-sm font-semibold text-zinc-100 tracking-tight">Upcoming Earnings</h2>
        <div className="flex gap-0.5 rounded-lg border border-zinc-800 bg-zinc-900 p-0.5 text-xs">
          {WINDOWS.map((w) => (
            <button
              key={w}
              onClick={() => setDays(w)}
              className={`rounded-md px-2.5 py-1 transition-colors duration-150 ease-out-quart tabular-nums ${
                days === w ? "bg-zinc-700 text-zinc-100 font-medium" : "text-zinc-500 hover:text-zinc-300"
              }`}
            >
              {w}d
            </button>
          ))}
        </div>
      </div>

      {loading && (
        <div className="flex items-center gap-2 py-4 text-zinc-500 text-sm">
          <span className="inline-block w-4 h-4 border-2 border-zinc-600 border-t-zinc-300 rounded-full animate-spin" />
          Loading earnings…
        </div>
      )}

      {!loading && error && (
        <div className="text-red-400 text-sm py-4">
          <p className="mb-1">{error}</p>
          <button
            onClick={() => setDays((d) => d)}
            className="text-zinc-500 hover:text-zinc-300 text-xs underline"
          >
            Retry
          </button>
        </div>
      )}

      {!loading && !error && earnings.length === 0 && (
        <p className="text-zinc-600 text-sm py-4">
          No earnings in the next {days} days for your watchlist.
        </p>
      )}

      {!loading && !error && earnings.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-[10px] font-semibold text-zinc-500 uppercase tracking-widest text-left">
                <th className="pb-2 pr-3 font-semibold">Ticker</th>
                <th className="pb-2 pr-3 font-semibold">Company</th>
                <th className="pb-2 pr-3 font-semibold">Date</th>
                <th className="pb-2 pr-3 font-semibold">Days Until</th>
                <th className="pb-2 pr-3 font-semibold text-right">Last Surprise</th>
                <th className="pb-2 pr-3 font-semibold text-right">Prior Surprise</th>
                <th className="pb-2 font-semibold text-right">Composite</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-900">
              {earnings.map((e) => {
                const blackout = e.days_until <= 2 && openPositions.has(e.ticker.toUpperCase());
                return (
                  <tr key={e.ticker} className="text-zinc-300">
                    <td className="py-2 pr-3">
                      <Link
                        href={`/stock/${e.ticker}`}
                        className="font-medium text-zinc-100 hover:text-white transition-colors duration-150 ease-out-quart"
                      >
                        {e.ticker}
                      </Link>
                    </td>
                    <td className="py-2 pr-3">
                      <div className="flex items-center gap-2">
                        <span className="text-zinc-400 max-w-[200px] truncate">
                          {e.company_name ?? "—"}
                        </span>
                        {blackout && (
                          <span className="shrink-0 rounded bg-yellow-500/15 px-1.5 py-0.5 text-[10px] font-medium text-yellow-400 whitespace-nowrap">
                            ⚠ Open position — earnings blackout active
                          </span>
                        )}
                      </div>
                    </td>
                    <td className="py-2 pr-3 tabular-nums text-zinc-400">{fmtDate(e.earnings_date)}</td>
                    <td className="py-2 pr-3 text-zinc-400">{fmtDaysUntil(e.days_until)}</td>
                    <td className={`py-2 pr-3 text-right tabular-nums ${surpriseColor(e.last_surprise_pct)}`}>
                      {fmtSurprise(e.last_surprise_pct)}
                    </td>
                    <td className={`py-2 pr-3 text-right tabular-nums ${surpriseColor(e.prior_surprise_pct)}`}>
                      {fmtSurprise(e.prior_surprise_pct)}
                    </td>
                    <td className={`py-2 text-right tabular-nums font-bold ${scoreColor(e.composite_score)}`}>
                      {e.composite_score == null ? "—" : Math.round(e.composite_score)}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
