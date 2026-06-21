"use client";
import { useEffect, useState } from "react";
import { CompanyInfo as CompanyInfoData } from "@/lib/types";
import { fetchCompany } from "@/lib/api";

// Cached company profile for the stock detail page: sector/industry, business
// summary and a few trader-relevant fields. (Recent earnings live in Financials.)
// Pure read of /api/company/{ticker} (7-day backend cache).

function fmtMarketCap(n: number | null): string | null {
  if (n == null || n <= 0) return null;
  if (n >= 1e12) return `$${(n / 1e12).toFixed(2)}T`;
  if (n >= 1e9) return `$${(n / 1e9).toFixed(2)}B`;
  if (n >= 1e6) return `$${(n / 1e6).toFixed(2)}M`;
  return `$${n.toLocaleString("en-US")}`;
}

function fmtPe(n: number | null): string | null {
  if (n == null || !isFinite(n) || n <= 0) return null;
  return n.toFixed(1);
}

function fmtDivYield(n: number | null): string | null {
  if (n == null || n <= 0) return null;
  // yfinance 1.3.0 returns dividendYield already as a percentage (e.g. 0.36 → 0.36%),
  // not a fraction — so display it as-is. Guard against an absurd out-of-range value.
  if (n > 100) return null;
  return `${n.toFixed(2)}%`;
}

function fmtPrice(n: number | null): string | null {
  if (n == null || n <= 0) return null;
  return `$${n.toFixed(2)}`;
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col">
      <span className="text-[10px] text-zinc-500 uppercase tracking-wider">{label}</span>
      <span className="text-sm font-semibold tabular-nums tracking-tight text-zinc-100">{value}</span>
    </div>
  );
}

const SUMMARY_LIMIT = 320;

export function CompanyInfo({ ticker }: { ticker: string }) {
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

  // Stay quiet while loading or if the lookup turned up nothing useful.
  if (loading) return null;
  if (!data) return null;

  const hasProfile = data.name || data.sector || data.industry || data.summary;
  const stats = [
    { label: "Market cap", value: fmtMarketCap(data.market_cap) },
    { label: "P/E (TTM)", value: fmtPe(data.trailing_pe) },
    { label: "Fwd P/E", value: fmtPe(data.forward_pe) },
    { label: "Div yield", value: fmtDivYield(data.dividend_yield) },
    {
      label: "52-wk range",
      value:
        fmtPrice(data.fifty_two_week_low) && fmtPrice(data.fifty_two_week_high)
          ? `${fmtPrice(data.fifty_two_week_low)} – ${fmtPrice(data.fifty_two_week_high)}`
          : null,
    },
  ].filter((s) => s.value) as { label: string; value: string }[];

  if (!hasProfile && stats.length === 0) return null;

  const summary = data.summary ?? "";
  const isLong = summary.length > SUMMARY_LIMIT;
  const shownSummary = expanded || !isLong ? summary : `${summary.slice(0, SUMMARY_LIMIT).trimEnd()}…`;

  return (
    <section className="bg-zinc-900 border border-zinc-800 rounded-xl p-5">
      <div className="mb-4">
        <h2 className="text-sm font-semibold text-zinc-100 tracking-tight">Company info</h2>
        {(data.sector || data.industry) && (
          <p className="text-xs text-zinc-500 mt-1">
            {data.sector ?? "—"}
            {data.industry ? <span className="text-zinc-600"> · {data.industry}</span> : null}
          </p>
        )}
      </div>

      {/* Key stats */}
      {stats.length > 0 && (
        <div className="flex flex-wrap gap-x-8 gap-y-3 mb-4">
          {stats.map((s) => (
            <Stat key={s.label} label={s.label} value={s.value} />
          ))}
        </div>
      )}

      {/* Business description */}
      {summary && (
        <div className="mb-4">
          <p className="text-sm text-zinc-300 leading-relaxed max-w-prose">{shownSummary}</p>
          {isLong && (
            <button
              onClick={() => setExpanded((v) => !v)}
              className="mt-1.5 text-xs font-medium text-zinc-400 hover:text-zinc-200 transition-colors duration-150 ease-out-quart"
            >
              {expanded ? "Show less" : "Show more"}
            </button>
          )}
        </div>
      )}
    </section>
  );
}
