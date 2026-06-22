// Maps a 0–100 composite score to a plain-English verdict band, mirroring the
// banded "Strong Buy / Buy / Hold / Avoid" cards on Stocky's stock detail page.
// Presentation only — the bands describe the existing composite score, they do
// not change any scoring logic.

export interface VerdictBand {
  label: string;
  rangeLabel: string;
  min: number;
  max: number;
  explanation: string;
  // tailwind text/border/bg classes for the active band
  text: string;
  border: string;
  bg: string;
  bar: string;
}

export const VERDICT_BANDS: VerdictBand[] = [
  {
    label: "Strong signal",
    rangeLabel: "85–100",
    min: 85,
    max: 100,
    explanation:
      "Multiple factors agree this stock has statistical momentum right now.",
    text: "text-emerald-400",
    border: "border-emerald-700/50",
    bg: "bg-emerald-950/30",
    bar: "bg-emerald-500",
  },
  {
    label: "Buy zone",
    rangeLabel: "63–84",
    min: 63,
    max: 84,
    explanation:
      "Most factors lean positive, with a few that are neutral or weak.",
    text: "text-emerald-300",
    border: "border-emerald-800/40",
    bg: "bg-emerald-950/20",
    bar: "bg-emerald-400",
  },
  {
    label: "Mixed signal",
    rangeLabel: "45–62",
    min: 45,
    max: 62,
    explanation:
      "Factors disagree — some bullish, some bearish — so there's no clear edge.",
    text: "text-amber-400",
    border: "border-amber-700/50",
    bg: "bg-amber-950/30",
    bar: "bg-amber-500",
  },
  {
    label: "Weak signal",
    rangeLabel: "0–44",
    min: 0,
    max: 44,
    explanation:
      "Factors are mostly contradicting or negative — little statistical support for buying.",
    text: "text-red-400",
    border: "border-red-700/50",
    bg: "bg-red-950/30",
    bar: "bg-red-500",
  },
];

export function verdictForScore(score: number): VerdictBand {
  return (
    VERDICT_BANDS.find((b) => score >= b.min && score <= b.max) ??
    VERDICT_BANDS[VERDICT_BANDS.length - 1]
  );
}
