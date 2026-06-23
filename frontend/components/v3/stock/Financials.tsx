"use client";
import { useEffect, useState } from "react";
import { CompanyInfo as CompanyInfoData } from "@/lib/types";
import { fetchCompany } from "@/lib/api";

// Financials panel for the stock detail page — sits directly beneath Company Info.
// Collapsed by default behind a "Show anyway" toggle.
// Groups: Valuation / Profitability & quality / Financial health / Market behavior,
// plus a Recent earnings table. Pure read of /api/company/{ticker}; shares the
// backend's 7-day cache with Company Info, so no extra yfinance fetch. Individual
// null fields are hidden per-ticker.

// Plain multiple (PEG, P/S, P/B, EV/EBITDA, current ratio, beta).
function fmtRatio(n: number | null | undefined): string | null {
  if (n == null || !isFinite(n)) return null;
  return n.toFixed(2);
}

// P/E — hide non-positive values (negative earnings make the multiple meaningless).
function fmtPe(n: number | null | undefined): string | null {
  if (n == null || !isFinite(n) || n <= 0) return null;
  return n.toFixed(1);
}

// yfinance reports margins / ROE / growth / payout as fractions (0.272 → 27.2%).
function fmtPct(n: number | null | undefined): string | null {
  if (n == null || !isFinite(n)) return null;
  return `${(n * 100).toFixed(1)}%`;
}

// yfinance debtToEquity is a percentage (79.5 → 0.80 D/E ratio).
function fmtDebtEquity(n: number | null | undefined): string | null {
  if (n == null || !isFinite(n)) return null;
  return (n / 100).toFixed(2);
}

// Signed currency in T/B/M (free cash flow can be negative).
function fmtCurrency(n: number | null | undefined): string | null {
  if (n == null || !isFinite(n)) return null;
  const sign = n < 0 ? "-" : "";
  const a = Math.abs(n);
  if (a >= 1e12) return `${sign}$${(a / 1e12).toFixed(2)}T`;
  if (a >= 1e9) return `${sign}$${(a / 1e9).toFixed(2)}B`;
  if (a >= 1e6) return `${sign}$${(a / 1e6).toFixed(2)}M`;
  return `${sign}$${a.toLocaleString("en-US")}`;
}

// Compact share count (47,300,354 → 47.3M).
function fmtVolume(n: number | null | undefined): string | null {
  if (n == null || !isFinite(n) || n <= 0) return null;
  if (n >= 1e9) return `${(n / 1e9).toFixed(2)}B`;
  if (n >= 1e6) return `${(n / 1e6).toFixed(2)}M`;
  if (n >= 1e3) return `${(n / 1e3).toFixed(1)}K`;
  return n.toLocaleString("en-US");
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col">
      <span className="text-[10px] text-zinc-500 uppercase tracking-wider">{label}</span>
      <span className="text-sm font-semibold tabular-nums tracking-tight text-zinc-100">{value}</span>
    </div>
  );
}

type StatItem = { label: string; value: string | null };
type StatGroup = { title: string; items: StatItem[] };

export function Financials({ ticker }: { ticker: string }) {
  const [data, setData] = useState<CompanyInfoData | null>(null);
  const [loading, setLoading] = useState(true);
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetchCompany(ticker)
      .then((d) => { if (!cancelled) setData(d); })
      .catch(() => { if (!cancelled) setData(null); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [ticker]);

  if (loading || !data) return null;

  // Build the four groups, dropping any field yfinance didn't populate for this ticker.
  const rawGroups: StatGroup[] = [
    {
      title: "Valuation",
      items: [
        { label: "P/E (TTM)", value: fmtPe(data.trailing_pe) },
        { label: "Fwd P/E", value: fmtPe(data.forward_pe) },
        { label: "PEG ratio", value: fmtRatio(data.peg_ratio) },
        { label: "Price/Sales", value: fmtRatio(data.price_to_sales) },
        { label: "Price/Book", value: fmtRatio(data.price_to_book) },
        { label: "EV/EBITDA", value: fmtRatio(data.ev_to_ebitda) },
      ],
    },
    {
      title: "Profitability & quality",
      items: [
        { label: "Profit margin", value: fmtPct(data.profit_margin) },
        { label: "Operating margin", value: fmtPct(data.operating_margin) },
        { label: "ROE", value: fmtPct(data.return_on_equity) },
        { label: "Revenue growth (YoY)", value: fmtPct(data.revenue_growth) },
      ],
    },
    {
      title: "Financial health",
      items: [
        { label: "Debt/Equity", value: fmtDebtEquity(data.debt_to_equity) },
        { label: "Current ratio", value: fmtRatio(data.current_ratio) },
        { label: "Free cash flow", value: fmtCurrency(data.free_cash_flow) },
      ],
    },
    {
      title: "Market behavior",
      items: [
        { label: "Beta", value: fmtRatio(data.beta) },
        { label: "Avg volume", value: fmtVolume(data.average_volume) },
        { label: "Payout ratio", value: fmtPct(data.payout_ratio) },
      ],
    },
  ];

  const groups = rawGroups
    .map((g) => ({ ...g, items: g.items.filter((i) => i.value) as { label: string; value: string }[] }))
    .filter((g) => g.items.length > 0);

  const earnings = data.earnings ?? [];

  // Nothing populated for this ticker → stay quiet (same convention as Company Info).
  if (groups.length === 0 && earnings.length === 0) return null;

  return (
    <section className="bg-zinc-900 border border-zinc-800 rounded-xl p-5">
      <button
        onClick={() => setExpanded((v) => !v)}
        aria-expanded={expanded}
        className="flex w-full items-center gap-2 text-left"
      >
        <svg
          width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"
          strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden
          className={`text-zinc-500 transition-transform duration-150 ease-out-quart ${expanded ? "rotate-90" : ""}`}
        >
          <path d="m9 18 6-6-6-6" />
        </svg>
        <h2 className="text-sm font-semibold text-zinc-100 tracking-tight">Financials</h2>
        <span className="ml-auto text-xs font-medium text-zinc-500">
          {expanded ? "Hide" : "Show"}
        </span>
      </button>

      {expanded && (
        <div className="mt-4 space-y-5">
          {groups.map((g) => (
            <div key={g.title}>
              <h3 className="text-[10px] uppercase tracking-wider text-zinc-500 mb-2.5">{g.title}</h3>
              <div className="flex flex-wrap gap-x-8 gap-y-3">
                {g.items.map((i) => (
                  <Stat key={i.label} label={i.label} value={i.value} />
                ))}
              </div>
            </div>
          ))}

          {/* Earnings — last ~4 quarters (date, EPS actual/estimate, surprise %). */}
          {earnings.length > 0 && (
            <div>
              <h3 className="text-[10px] uppercase tracking-wider text-zinc-500 mb-2.5">Earnings</h3>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-[10px] uppercase tracking-wider text-zinc-500 border-b border-zinc-800">
                      <th className="text-left font-medium py-1.5 pr-4">Quarter</th>
                      <th className="text-right font-medium py-1.5 px-4">EPS actual</th>
                      <th className="text-right font-medium py-1.5 px-4">EPS est.</th>
                      <th className="text-right font-medium py-1.5 pl-4">Surprise</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-zinc-800/60">
                    {earnings.map((q) => {
                      const surprisePos = q.surprise_pct != null && q.surprise_pct >= 0;
                      return (
                        <tr key={q.date} className="text-zinc-200">
                          <td className="text-left py-1.5 pr-4 tabular-nums text-zinc-400">{q.date}</td>
                          <td className="text-right py-1.5 px-4 tabular-nums">
                            {q.eps_actual != null ? q.eps_actual.toFixed(2) : "—"}
                          </td>
                          <td className="text-right py-1.5 px-4 tabular-nums text-zinc-400">
                            {q.eps_estimate != null ? q.eps_estimate.toFixed(2) : "—"}
                          </td>
                          <td
                            className={`text-right py-1.5 pl-4 tabular-nums font-medium ${
                              q.surprise_pct == null
                                ? "text-zinc-500"
                                : surprisePos
                                ? "text-emerald-400"
                                : "text-red-400"
                            }`}
                          >
                            {q.surprise_pct != null
                              ? `${surprisePos ? "+" : ""}${q.surprise_pct.toFixed(1)}%`
                              : "—"}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>
      )}
    </section>
  );
}
