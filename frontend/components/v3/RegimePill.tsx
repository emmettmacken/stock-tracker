export function RegimePill({ regime }: { regime: "bull" | "bear" | "transition" }) {
  const cfg = {
    bull:       { cls: "bg-emerald-900/60 text-emerald-300 border-emerald-700/50", label: "▲ Bull" },
    bear:       { cls: "bg-red-900/60 text-red-300 border-red-700/50",            label: "▼ Bear" },
    transition: { cls: "bg-zinc-800 text-zinc-300 border-zinc-600",               label: "↔ Transition" },
  }[regime];
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-semibold tracking-wide border ${cfg.cls}`}>
      {cfg.label}
    </span>
  );
}
