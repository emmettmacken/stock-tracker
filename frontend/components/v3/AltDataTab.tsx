"use client";
import { useState, useEffect, useRef } from "react";
import { SentimentData, InsiderData, ShortInterestData } from "@/lib/types";
import { fetchSentiment, fetchInsider, fetchShortInterest } from "@/lib/api";
import { SkeletonCard } from "./Skeleton";

function SectionHeader({ title }: { title: string }) {
  return (
    <h4 className="text-[10px] font-semibold text-zinc-500 uppercase tracking-widest mb-2">
      {title}
    </h4>
  );
}

function Unavailable({ reason }: { reason?: string }) {
  return (
    <p className="text-zinc-600 text-xs italic">
      {reason ?? "Not available"}
    </p>
  );
}

function RetryButton({ onClick }: { onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className="text-zinc-500 hover:text-zinc-300 text-[10px] underline mt-1"
    >
      Retry
    </button>
  );
}

function SentimentSection({ ticker }: { ticker: string }) {
  const [data, setData] = useState<SentimentData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  function load() {
    setLoading(true);
    setError(null);
    fetchSentiment(ticker)
      .then(setData)
      .catch((e) => setError(e instanceof Error ? e.message : "Failed"))
      .finally(() => setLoading(false));
  }

  useEffect(() => { load(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  if (loading) return <SkeletonCard />;
  if (error) return (
    <div>
      <p className="text-red-400 text-xs">{error}</p>
      <RetryButton onClick={load} />
    </div>
  );
  if (!data?.available) return (
    <Unavailable reason={
      data?.reason === "no_key"
        ? "Not available — add ALPHA_VANTAGE_KEY to backend .env"
        : data?.reason === "rate_limited"
        ? "Rate limited — retry in a minute"
        : undefined
    } />
  );

  const dirColor = data.direction === "bullish"
    ? "text-emerald-400" : data.direction === "bearish"
    ? "text-red-400" : "text-zinc-400";

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-3">
        <span className={`text-2xl font-bold tabular-nums ${dirColor}`}>
          {data.sentiment_score?.toFixed(1)}
        </span>
        <div>
          <span className={`text-xs font-semibold capitalize ${dirColor}`}>
            {data.direction}
          </span>
          <p className="text-zinc-600 text-[10px]">sentiment score / 100</p>
        </div>
      </div>
      <div className="h-1.5 bg-zinc-800 rounded-full overflow-hidden">
        <div
          className={`h-full rounded-full ${
            data.direction === "bullish" ? "bg-emerald-500"
            : data.direction === "bearish" ? "bg-red-500"
            : "bg-zinc-500"
          }`}
          style={{ width: `${data.sentiment_score ?? 0}%` }}
        />
      </div>
      <div className="grid grid-cols-2 gap-2 mt-2">
        {data.article_count != null && (
          <div className="bg-zinc-800 rounded p-2">
            <div className="text-zinc-500 text-[10px]">Articles (7d)</div>
            <div className="text-zinc-200 font-semibold">{data.article_count}</div>
          </div>
        )}
        {data.buzz_score != null && (
          <div className="bg-zinc-800 rounded p-2">
            <div className="text-zinc-500 text-[10px]">Buzz Score</div>
            <div className="text-zinc-200 font-semibold">{data.buzz_score?.toFixed(2)}</div>
          </div>
        )}
        {data.bearish_pct != null && (
          <div className="bg-zinc-800 rounded p-2">
            <div className="text-zinc-500 text-[10px]">Bearish %</div>
            <div className="text-red-400 font-semibold">{data.bearish_pct?.toFixed(1)}%</div>
          </div>
        )}
        {data.sector_vs_avg != null && (
          <div className="bg-zinc-800 rounded p-2">
            <div className="text-zinc-500 text-[10px]">Sector avg bull%</div>
            <div className="text-zinc-200 font-semibold">
              {((data.sector_vs_avg ?? 0) * 100).toFixed(1)}%
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function InsiderSection({ ticker }: { ticker: string }) {
  const [data, setData] = useState<InsiderData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  function load() {
    setLoading(true);
    setError(null);
    fetchInsider(ticker)
      .then(setData)
      .catch((e) => setError(e instanceof Error ? e.message : "Failed"))
      .finally(() => setLoading(false));
  }

  useEffect(() => { load(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  if (loading) return <SkeletonCard />;
  if (error) return (
    <div>
      <p className="text-red-400 text-xs">{error}</p>
      <RetryButton onClick={load} />
    </div>
  );
  if (!data?.available) return <Unavailable />;

  const netShares = data.net_shares ?? 0;
  const isBuying = netShares > 0;
  const barColor = isBuying ? "bg-emerald-500" : "bg-red-500";
  const textColor = isBuying ? "text-emerald-400" : "text-red-400";
  const maxBar = Math.abs(netShares);

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        <span className={`text-xs font-semibold uppercase tracking-wide ${textColor}`}>
          {data.direction}
        </span>
        <span className="text-zinc-500 text-[10px]">
          {data.transaction_count} transaction{data.transaction_count !== 1 ? "s" : ""} in {data.period_days}d
        </span>
      </div>
      <div>
        <div className="flex justify-between text-[10px] text-zinc-500 mb-1">
          <span>Net shares</span>
          <span className={textColor}>
            {netShares >= 0 ? "+" : ""}{netShares.toLocaleString()}
          </span>
        </div>
        {/* Net buy/sell bar centred at 0 */}
        <div className="h-2 bg-zinc-800 rounded-full overflow-hidden">
          <div className="h-full flex">
            {isBuying ? (
              <>
                <div className="w-1/2" />
                <div
                  className={`h-full rounded-r-full ${barColor}`}
                  style={{ width: `${Math.min(maxBar / 1e6 * 50, 50)}%` }}
                />
              </>
            ) : (
              <>
                <div
                  className={`h-full rounded-l-full ${barColor}`}
                  style={{
                    marginLeft: "auto",
                    width: `${Math.min(maxBar / 1e6 * 50, 50)}%`,
                  }}
                />
                <div className="w-1/2" />
              </>
            )}
          </div>
        </div>
        <div className="flex justify-between text-[9px] text-zinc-700 mt-0.5">
          <span>◀ Selling</span>
          <span>Buying ▶</span>
        </div>
      </div>
    </div>
  );
}

function ShortSection({ ticker }: { ticker: string }) {
  const [data, setData] = useState<ShortInterestData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  function load() {
    setLoading(true);
    setError(null);
    fetchShortInterest(ticker)
      .then(setData)
      .catch((e) => setError(e instanceof Error ? e.message : "Failed"))
      .finally(() => setLoading(false));
  }

  useEffect(() => { load(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  if (loading) return <SkeletonCard />;
  if (error) return (
    <div>
      <p className="text-red-400 text-xs">{error}</p>
      <RetryButton onClick={load} />
    </div>
  );
  if (!data?.available) return <Unavailable />;

  const floatPct = data.short_float_pct;
  const isHigh = data.high_short_interest;

  return (
    <div className="space-y-2">
      {isHigh && (
        <div className="flex items-center gap-1.5 text-amber-400 text-[10px] font-medium bg-amber-950/30 border border-amber-800/40 rounded px-2 py-1">
          <span>⚠</span> High short interest — potential squeeze setup
        </div>
      )}
      <div className="grid grid-cols-3 gap-2">
        <div className="bg-zinc-800 rounded p-2">
          <div className="text-zinc-500 text-[10px]">Short Float</div>
          <div className={`font-semibold ${isHigh ? "text-amber-400" : "text-zinc-200"}`}>
            {floatPct != null ? `${floatPct.toFixed(1)}%` : "—"}
          </div>
        </div>
        <div className="bg-zinc-800 rounded p-2">
          <div className="text-zinc-500 text-[10px]">Short Ratio</div>
          <div className="text-zinc-200 font-semibold">
            {data.short_ratio != null ? data.short_ratio.toFixed(1) : "—"}
          </div>
        </div>
        <div className="bg-zinc-800 rounded p-2">
          <div className="text-zinc-500 text-[10px]">Shares Short</div>
          <div className="text-zinc-200 font-semibold text-[11px]">
            {data.shares_short != null
              ? data.shares_short >= 1e6
                ? `${(data.shares_short / 1e6).toFixed(1)}M`
                : data.shares_short.toLocaleString()
              : "—"}
          </div>
        </div>
      </div>
      {floatPct != null && (
        <div>
          <div className="h-1.5 bg-zinc-800 rounded-full overflow-hidden">
            <div
              className={`h-full rounded-full ${floatPct > 20 ? "bg-amber-500" : "bg-zinc-500"}`}
              style={{ width: `${Math.min(floatPct, 100)}%` }}
            />
          </div>
          <div className="flex justify-between text-[9px] text-zinc-700 mt-0.5">
            <span>0%</span>
            <span>20%</span>
            <span>100%</span>
          </div>
        </div>
      )}
    </div>
  );
}

export function AltDataTab({ ticker }: { ticker: string }) {
  const loaded = useRef(false);
  useEffect(() => { loaded.current = true; }, []);

  return (
    <div className="space-y-5 text-xs">
      <div>
        <SectionHeader title="News Sentiment" />
        <SentimentSection ticker={ticker} />
      </div>
      <div className="border-t border-zinc-800 pt-4">
        <SectionHeader title="Insider Activity (30d)" />
        <InsiderSection ticker={ticker} />
      </div>
      <div className="border-t border-zinc-800 pt-4">
        <SectionHeader title="Short Interest" />
        <ShortSection ticker={ticker} />
      </div>
    </div>
  );
}
