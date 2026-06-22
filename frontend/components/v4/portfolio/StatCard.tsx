// Small stat card used across the Portfolio page (account summary + positions summary).
// Matches the app's panel style: zinc-900 surface, zinc-800 border, rounded-xl.
export function StatCard({
  label,
  value,
  sub,
  valueClass = "text-zinc-100",
}: {
  label: string;
  value: React.ReactNode;
  sub?: React.ReactNode;
  valueClass?: string;
}) {
  return (
    <div className="bg-zinc-900 border border-zinc-800 rounded-xl px-4 py-3 flex flex-col gap-1">
      <span className="text-[10px] text-zinc-500 uppercase tracking-wider">{label}</span>
      <span className={`text-lg font-bold tabular-nums tracking-tight ${valueClass}`}>{value}</span>
      {sub && <span className="text-[10px] text-zinc-600 tabular-nums">{sub}</span>}
    </div>
  );
}
