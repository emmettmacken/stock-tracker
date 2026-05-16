"use client";
import { useState, useEffect } from "react";
import { FactorScoreData } from "@/lib/types";
import { scoreTextColor, scoreBarColor } from "./FactorScorePill";

const FACTOR_META: Record<string, { label: string; explanation: string }> = {
  hmm: {
    label: "Markov / HMM Signal",
    explanation:
      "Probability the stock transitions to a bullish price state next period, estimated by a 2-state hidden Markov model fitted on daily returns and volume.",
  },
  momentum: {
    label: "Momentum",
    explanation:
      "Average of 3-month and 12-month price returns, with the most recent month skipped to avoid short-term reversal. Z-scored against the trailing 252-day distribution.",
  },
  vol_trend: {
    label: "Vol-Adjusted Trend",
    explanation:
      "Degree to which price > 20-day MA > 50-day MA > 200-day MA (full alignment = fully bullish), weighted by inverse of recent realised volatility so noisy stocks contribute less.",
  },
  earnings: {
    label: "Earnings Momentum",
    explanation:
      "EPS surprise vs analyst consensus estimate over the last two reported quarters — consecutive positive beats score high; misses score low.",
  },
  sentiment: {
    label: "News Sentiment",
    explanation:
      "Aggregate sentiment score from recent news articles via Alpha Vantage. Bullish coverage scores high; bearish scores low. Shows N/A without an ALPHA_VANTAGE_KEY.",
  },
  insider: {
    label: "Insider Activity",
    explanation:
      "Net insider buying vs selling over the past 30 days from SEC Form 4 filings. Net buying scores 70, net selling scores 30, no activity scores 50.",
  },
};

const DEFAULT_WEIGHTS: Record<string, number> = {
  hmm: 10,
  momentum: 35,
  vol_trend: 25,
  earnings: 20,
  sentiment: 0,
  insider: 10,
};

function loadWeights(ticker: string): Record<string, number> {
  if (typeof window === "undefined") return { ...DEFAULT_WEIGHTS };
  try {
    const raw = localStorage.getItem(`v3-factor-weights-${ticker}`);
    if (!raw) return { ...DEFAULT_WEIGHTS };
    return { ...DEFAULT_WEIGHTS, ...JSON.parse(raw) };
  } catch {
    return { ...DEFAULT_WEIGHTS };
  }
}

function saveWeights(ticker: string, w: Record<string, number>) {
  if (typeof window === "undefined") return;
  localStorage.setItem(`v3-factor-weights-${ticker}`, JSON.stringify(w));
}

function computeComposite(
  factors: FactorScoreData["factors"],
  weights: Record<string, number>
): number {
  const keys = Object.keys(factors) as (keyof typeof factors)[];
  const available = keys.filter((k) => !factors[k].null && factors[k].score !== null);
  const totalW = available.reduce((s, k) => s + (weights[k] ?? 0), 0);
  if (totalW === 0) return 0;
  return available.reduce(
    (s, k) => s + ((factors[k].score ?? 0) * (weights[k] ?? 0)) / totalW,
    0
  );
}

export function FactorsTab({
  data,
  ticker,
}: {
  data: FactorScoreData;
  ticker: string;
}) {
  const [weights, setWeights] = useState<Record<string, number>>(DEFAULT_WEIGHTS);
  const [openTip, setOpenTip] = useState<string | null>(null);

  useEffect(() => {
    setWeights(loadWeights(ticker));
  }, [ticker]);

  const composite = computeComposite(data.factors, weights);
  const factorKeys = Object.keys(FACTOR_META) as (keyof typeof data.factors)[];

  function updateWeight(key: string, val: number) {
    const next = { ...weights, [key]: val };
    setWeights(next);
    saveWeights(ticker, next);
  }

  return (
    <div className="space-y-5 text-xs">
      {/* Composite score header */}
      <div className="flex items-center gap-3 pb-3 border-b border-zinc-800">
        <span className={`text-4xl font-bold tabular-nums ${scoreTextColor(composite)}`}>
          {composite.toFixed(1)}
        </span>
        <div>
          <div className="text-zinc-300 font-medium">Composite Score</div>
          <div className="text-zinc-500 text-[10px] mt-0.5">
            Drag sliders to reweight · saved per ticker
          </div>
        </div>
      </div>

      {/* Factor rows */}
      {factorKeys.map((key) => {
        const f = data.factors[key];
        const meta = FACTOR_META[key];
        const isNull = f.null;
        const score = f.score ?? 0;
        const tipOpen = openTip === key;

        return (
          <div key={key} className="space-y-1.5">
            {/* Label + score */}
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-1.5">
                <span className="text-zinc-200 font-medium">{meta.label}</span>
                {isNull && (
                  <span className="text-[9px] font-semibold bg-zinc-700 text-zinc-500 rounded px-1 py-0.5 tracking-wide">
                    N/A
                  </span>
                )}
                <button
                  onClick={() => setOpenTip(tipOpen ? null : key)}
                  className="text-zinc-600 hover:text-zinc-400 transition-colors text-[11px] leading-none"
                  title="What is this?"
                >
                  ⓘ
                </button>
              </div>
              <span className={`font-semibold tabular-nums ${isNull ? "text-zinc-600" : scoreTextColor(score)}`}>
                {isNull ? "—" : score.toFixed(1)}
              </span>
            </div>

            {/* Expandable explanation */}
            {tipOpen && (
              <div className="text-zinc-400 bg-zinc-800/60 border border-zinc-700/50 rounded px-2.5 py-2 leading-relaxed">
                {meta.explanation}
              </div>
            )}

            {/* Score bar */}
            <div className="h-1.5 bg-zinc-800 rounded-full overflow-hidden">
              <div
                className={`h-full rounded-full transition-all duration-300 ${
                  isNull ? "bg-zinc-700" : scoreBarColor(score)
                }`}
                style={{ width: isNull ? "0%" : `${score}%` }}
              />
            </div>

            {/* Weight slider */}
            <div className="flex items-center gap-2">
              <span className="text-zinc-600 w-14 shrink-0">
                wt: {weights[key] ?? 0}
              </span>
              <input
                type="range"
                min={0}
                max={100}
                value={weights[key] ?? 0}
                onChange={(e) => updateWeight(key, parseInt(e.target.value))}
                disabled={isNull}
                className="flex-1 accent-zinc-400 h-0.5 cursor-pointer disabled:opacity-30 disabled:cursor-not-allowed"
              />
            </div>
          </div>
        );
      })}

      <p className="text-zinc-600 text-[10px] pt-1">
        Weights are relative — they&apos;re normalised before computing the composite. Null factors are excluded.
      </p>
    </div>
  );
}
