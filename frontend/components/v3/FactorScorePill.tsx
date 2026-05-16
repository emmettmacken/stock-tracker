interface Props {
  score: number;
  size?: "sm" | "md";
}

function colorClass(score: number, size: "sm" | "md") {
  const base = size === "md"
    ? "inline-flex items-center justify-center rounded-full border text-sm font-bold w-10 h-10"
    : "inline-flex items-center justify-center rounded-md px-1.5 py-0.5 text-[11px] font-bold border";
  if (score <= 35) return `${base} text-red-400 border-red-700/50 bg-red-950/30`;
  if (score <= 64) return `${base} text-amber-400 border-amber-700/50 bg-amber-950/30`;
  return `${base} text-emerald-400 border-emerald-700/50 bg-emerald-950/30`;
}

export function FactorScorePill({ score, size = "sm" }: Props) {
  return (
    <span className={colorClass(score, size)} title="Composite factor score (0–100)">
      {Math.round(score)}
    </span>
  );
}

export function scoreTextColor(score: number) {
  if (score <= 35) return "text-red-400";
  if (score <= 64) return "text-amber-400";
  return "text-emerald-400";
}

export function scoreBarColor(score: number) {
  if (score <= 35) return "bg-red-500";
  if (score <= 64) return "bg-amber-500";
  return "bg-emerald-500";
}
