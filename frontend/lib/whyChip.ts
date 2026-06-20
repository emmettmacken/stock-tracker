import { SnapshotData } from "./types";

// Display-only: surfaces the single most influential factor (when buy-eligible) or the
// dominant blocking reason, derived entirely from the cached snapshot. No computation.

const FACTOR_PHRASES: Record<string, string> = {
  momentum: "Strong momentum",
  hmm: "Bullish regime",
  vol_trend: "Uptrend",
  earnings: "Earnings strength",
  insider: "Insider buying",
  sentiment: "Positive sentiment",
};

// Buy-zone bar mirrors the verdict bands (70 = start of "Buy zone").
export const BUY_ZONE_THRESHOLD = 70;

export type ChipTone = "pos" | "neg" | "neutral";

export function whyChip(s: SnapshotData): { label: string; tone: ChipTone } | null {
  const f = s.factors;
  const score = s.composite_score;
  if (!f || score === null) return null;

  if (score >= BUY_ZONE_THRESHOLD) {
    // Surface the strongest non-null factor.
    let best: { key: string; val: number } | null = null;
    for (const [key, det] of Object.entries(f.factors)) {
      if (!det || det.null || det.score == null) continue;
      if (!best || det.score > best.val) best = { key, val: det.score };
    }
    if (best && FACTOR_PHRASES[best.key]) return { label: FACTOR_PHRASES[best.key], tone: "pos" };
    return { label: "Buy zone", tone: "pos" };
  }

  if (s.hmm_regime === "bear") return { label: "Bearish regime", tone: "neg" };
  return { label: "Score below threshold", tone: "neutral" };
}
