"use client";
import { useEffect, useState } from "react";
import { FactorScoreData, InsiderData, SentimentData } from "@/lib/types";
import { scoreTextColor, scoreBarColor } from "@/components/v3/FactorScorePill";
import { fetchInsider, fetchSentiment } from "@/lib/api";

// The five factors shown in the breakdown, in display order. Plain-English
// descriptions written for a non-quant reader.
const FACTORS: { key: FactorKey; label: string; explanation: string }[] = [
  {
    key: "momentum",
    label: "Momentum",
    explanation:
      "How strong this stock's price trend has been over the last 3 and 12 months. Stocks that have been rising tend to keep rising in the short term.",
  },
  {
    key: "vol_trend",
    label: "Vol-adjusted trend",
    explanation:
      "Whether the price is above its short, medium, and long-term moving averages, weighted down for stocks that are unusually jumpy.",
  },
  {
    key: "earnings",
    label: "Earnings",
    explanation:
      "Whether the company beat or missed analyst expectations in its last two earnings reports.",
  },
  {
    key: "hmm",
    label: "Markov / HMM",
    explanation:
      "Looks at the pattern of recent up/down days and estimates whether this specific stock's price behavior currently favors more upside or downside.",
  },
  {
    key: "insider",
    label: "Insider activity",
    explanation:
      "Whether company executives have been net buying or selling their own shares recently, based on public SEC filings.",
  },
  {
    key: "sentiment",
    label: "News sentiment",
    explanation:
      "Whether recent news coverage about this stock has been positive or negative in tone, based on the last 7 days of articles.",
  },
];

type FactorKey = "momentum" | "vol_trend" | "earnings" | "hmm" | "insider" | "sentiment";

export function FactorBreakdown({ data }: { data: FactorScoreData }) {
  // Raw detail for the insider and sentiment factors comes from the same
  // Alpha Vantage / EDGAR endpoints the factor scores are derived from.
  const [insider, setInsider] = useState<InsiderData | null>(null);
  const [sentiment, setSentiment] = useState<SentimentData | null>(null);
  useEffect(() => {
    fetchInsider(data.ticker).then(setInsider).catch(() => setInsider(null));
    fetchSentiment(data.ticker).then(setSentiment).catch(() => setSentiment(null));
  }, [data.ticker]);

  // Display weight = share among the non-null factors shown here.
  const totalWeight = FACTORS.reduce((s, { key }) => {
    const f = data.factors[key];
    return f && !f.null ? s + f.weight : s;
  }, 0);

  return (
    <div className="space-y-5">
      {FACTORS.map(({ key, label, explanation }) => {
        const f = data.factors[key];
        if (!f) return null;
        const isNull = f.null || f.score === null;
        const score = f.score ?? 0;
        const weightPct = !isNull && totalWeight > 0 ? Math.round((f.weight / totalWeight) * 100) : null;

        return (
          <div key={key} className="space-y-1.5">
            <div className="flex items-center justify-between gap-2">
              <div className="flex items-center gap-2">
                <span className="text-sm font-semibold text-zinc-100">{label}</span>
                {weightPct !== null ? (
                  <span className="text-[10px] font-medium text-zinc-400 bg-zinc-800 rounded px-1.5 py-0.5">
                    {weightPct}% weight
                  </span>
                ) : (
                  <span className="text-[10px] font-medium text-zinc-500 bg-zinc-800 rounded px-1.5 py-0.5">
                    excluded
                  </span>
                )}
              </div>
              <span className={`text-sm font-bold tabular-nums ${isNull ? "text-zinc-600" : scoreTextColor(score)}`}>
                {isNull ? "—" : score.toFixed(0)}
              </span>
            </div>

            <p className="text-xs text-zinc-400 leading-relaxed">{explanation}</p>

            {isNull ? (
              <div className="text-xs text-zinc-500 italic bg-zinc-900/60 border border-zinc-800 rounded px-2.5 py-1.5">
                Not available — excluded from score
              </div>
            ) : (
              <>
                <div className="h-2 bg-zinc-800 rounded-full overflow-hidden">
                  <div
                    className={`h-full rounded-full transition-[width] duration-500 ease-out-quart ${scoreBarColor(score)}`}
                    style={{ width: `${score}%` }}
                  />
                </div>
                <RawNumbers factorKey={key} data={data} insider={insider} sentiment={sentiment} />
              </>
            )}
          </div>
        );
      })}
    </div>
  );
}

function RawNumbers({
  factorKey,
  data,
  insider,
  sentiment,
}: {
  factorKey: FactorKey;
  data: FactorScoreData;
  insider: InsiderData | null;
  sentiment: SentimentData | null;
}) {
  if (factorKey === "momentum") {
    return (
      <div className="flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-zinc-500">
        <Stat label="3-month return" value={pct(data.ret_3m)} signed={data.ret_3m} />
        <Stat label="12-month return" value={pct(data.ret_12m)} signed={data.ret_12m} />
      </div>
    );
  }

  if (factorKey === "vol_trend") {
    const d = data.vol_trend_detail;
    if (!d) return null;
    return (
      <div className="flex flex-wrap gap-2 text-[11px]">
        <MaBadge label="20-day MA" price={d.price} ma={d.ma20} />
        <MaBadge label="50-day MA" price={d.price} ma={d.ma50} />
        <MaBadge label="200-day MA" price={d.price} ma={d.ma200} />
      </div>
    );
  }

  if (factorKey === "earnings") {
    const s = data.earnings_detail?.surprises;
    if (!s || s.length === 0) return null;
    // Backend orders oldest → newest; show newest first.
    const labelled = s.map((v, i) => ({ v, q: i === s.length - 1 ? "Latest" : "Prior" })).reverse();
    return (
      <div className="flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-zinc-500">
        {labelled.map(({ v, q }, i) => (
          <Stat key={i} label={`${q} quarter surprise`} value={pct(v)} signed={v} />
        ))}
      </div>
    );
  }

  if (factorKey === "hmm") {
    return (
      <div className="flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-zinc-500">
        <span>
          Signal <span className="text-zinc-300 font-medium">{data.hmm_signal}</span>
        </span>
        <span>
          Confidence{" "}
          <span className="text-zinc-300 font-medium tabular-nums">
            {(data.hmm_confidence * 100).toFixed(0)}%
          </span>
        </span>
        <span className="text-zinc-600">see Markov detail below</span>
      </div>
    );
  }

  if (factorKey === "insider") {
    if (!insider?.available || insider.net_shares == null) {
      return <div className="text-[11px] text-zinc-600">No recent Form 4 filings.</div>;
    }
    const net = insider.net_shares;
    const buying = net > 0;
    return (
      <div className="flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-zinc-500">
        <span>
          Net shares{" "}
          <span className={`font-medium tabular-nums ${buying ? "text-emerald-400" : "text-red-400"}`}>
            {net >= 0 ? "+" : ""}
            {net.toLocaleString()}
          </span>
        </span>
        <span className="capitalize">
          {insider.transaction_count} txn{insider.transaction_count !== 1 ? "s" : ""}
          {insider.period_days != null ? ` in ${insider.period_days}d` : ""}
        </span>
      </div>
    );
  }

  if (factorKey === "sentiment") {
    if (!sentiment?.available || sentiment.sentiment_score == null) return null;
    const dirColor =
      sentiment.direction === "bullish"
        ? "text-emerald-400"
        : sentiment.direction === "bearish"
        ? "text-red-400"
        : "text-zinc-300";
    return (
      <div className="flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-zinc-500">
        <span>
          Sentiment{" "}
          <span className={`font-medium tabular-nums ${dirColor}`}>
            {sentiment.sentiment_score.toFixed(1)} / 100
          </span>
        </span>
        {sentiment.direction && (
          <span className={`capitalize font-medium ${dirColor}`}>{sentiment.direction}</span>
        )}
        {sentiment.article_count != null && (
          <span>
            <span className="text-zinc-300 font-medium tabular-nums">{sentiment.article_count}</span>{" "}
            article{sentiment.article_count !== 1 ? "s" : ""} (7d)
          </span>
        )}
      </div>
    );
  }

  return null;
}

function Stat({ label, value, signed }: { label: string; value: string; signed?: number | null }) {
  const color = signed == null ? "text-zinc-300" : signed >= 0 ? "text-emerald-400" : "text-red-400";
  return (
    <span>
      {label} <span className={`font-medium tabular-nums ${color}`}>{value}</span>
    </span>
  );
}

function MaBadge({ label, price, ma }: { label: string; price: number; ma: number }) {
  const above = price >= ma;
  return (
    <span
      className={`inline-flex items-center gap-1 rounded px-1.5 py-0.5 border ${
        above
          ? "text-emerald-400 border-emerald-800/50 bg-emerald-950/20"
          : "text-red-400 border-red-800/50 bg-red-950/20"
      }`}
    >
      {above ? "▲ above" : "▼ below"} {label}
      <span className="text-zinc-500">(${ma.toFixed(2)})</span>
    </span>
  );
}

function pct(v: number | null | undefined): string {
  if (v == null) return "—";
  return `${v >= 0 ? "+" : ""}${(v * 100).toFixed(1)}%`;
}
