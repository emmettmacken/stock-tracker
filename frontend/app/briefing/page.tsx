"use client";
import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { Briefing } from "@/lib/types";
import { fetchBriefing } from "@/lib/api";

function eur(n: number) {
  return `€${n.toLocaleString("en-IE", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

// Builds the narrative paragraph from the aggregated run data (templating only).
function buildNarrative(b: Briefing): string {
  const parts: string[] = [];
  parts.push(`The system evaluated ${b.evaluated_count} ticker${b.evaluated_count === 1 ? "" : "s"} in the most recent run.`);

  if (b.orders.length) {
    const list = b.orders
      .map((o) => `${o.ticker}${o.price != null ? ` at ${eur(o.price)}` : ""}`)
      .join(", ");
    parts.push(`${b.orders.length} order${b.orders.length === 1 ? "" : "s"} placed: ${list}.`);
  } else {
    parts.push("No orders were placed.");
  }

  if (b.skip_breakdown.length) {
    const top = b.skip_breakdown
      .slice(0, 4)
      .map((s) => `${s.count} by ${s.label.toLowerCase()}`)
      .join(", ");
    parts.push(`Blocks: ${top}.`);
  }

  if (b.positions_closed > 0) {
    parts.push(`${b.positions_closed} position${b.positions_closed === 1 ? "" : "s"} closed.`);
  }

  if (b.macro_flags.length) {
    parts.push(b.macro_flags.join(" "));
  }

  if (b.account.available && b.account.equity != null) {
    const chg = b.account.equity_change_pct;
    parts.push(
      `Account equity: ${eur(b.account.equity)}${
        chg != null ? ` (today ${chg >= 0 ? "+" : ""}${chg.toFixed(2)}%)` : ""
      }.`
    );
  }

  return parts.join(" ");
}

export default function BriefingPage() {
  const [data, setData] = useState<Briefing | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchBriefing()
      .then((d) => { if (!cancelled) setData(d); })
      .catch((e) => { if (!cancelled) setError(e instanceof Error ? e.message : "Failed to load briefing"); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, []);

  const maxSkip = useMemo(
    () => (data?.skip_breakdown.length ? Math.max(...data.skip_breakdown.map((s) => s.count)) : 0),
    [data]
  );

  return (
    <main className="min-h-screen bg-zinc-950 text-zinc-100">
      <div className="max-w-3xl mx-auto px-4 py-8 sm:py-10 space-y-6">
        <header>
          <h1 className="text-2xl sm:text-[28px] font-semibold tracking-tight text-white text-balance">
            Today&apos;s Briefing
          </h1>
          <p className="mt-1.5 text-zinc-400 text-sm leading-relaxed">
            A plain-English summary of the most recent signal-job run
            {data?.run_at && (
              <span className="text-zinc-500"> · {new Date(data.run_at).toLocaleString()}</span>
            )}
          </p>
        </header>

        {loading ? (
          <div className="flex items-center justify-center py-24 gap-2.5 text-zinc-500 text-sm">
            <span className="inline-block w-5 h-5 border-2 border-zinc-800 border-t-zinc-400 rounded-full animate-spin" />
            Loading briefing…
          </div>
        ) : error ? (
          <p className="text-red-400 text-sm py-12 text-center">{error}</p>
        ) : !data || !data.available ? (
          <div className="rounded-xl border border-dashed border-zinc-800 py-12 text-center">
            <p className="text-sm text-zinc-400">No signal-job run to summarise yet</p>
            <p className="mt-1 text-xs text-zinc-600">Run the signals job from the Automation page first.</p>
          </div>
        ) : (
          <>
            {/* Narrative */}
            <section className="bg-zinc-900 border border-zinc-800 rounded-xl p-5">
              <p className="text-sm text-zinc-200 leading-relaxed">{buildNarrative(data)}</p>
            </section>

            {/* Orders */}
            {data.orders.length > 0 && (
              <section className="bg-zinc-900 border border-zinc-800 rounded-xl p-5">
                <h2 className="text-sm font-semibold text-zinc-100 tracking-tight mb-3">Orders placed</h2>
                <div className="space-y-1.5">
                  {data.orders.map((o) => (
                    <Link
                      key={o.ticker}
                      href={`/stock/${o.ticker}`}
                      className="flex items-center justify-between rounded-lg bg-emerald-950/20 border border-emerald-900/30 px-3 py-2 hover:bg-emerald-950/30 transition-colors"
                    >
                      <span className="text-sm font-semibold text-emerald-300">{o.ticker}</span>
                      <span className="text-xs text-zinc-400 tabular-nums">
                        {o.price != null ? eur(o.price) : "—"}
                        {o.score != null && <span className="text-zinc-600"> · score {o.score.toFixed(1)}</span>}
                        {o.sizing_method && <span className="text-zinc-600"> · {o.sizing_method}</span>}
                      </span>
                    </Link>
                  ))}
                </div>
              </section>
            )}

            {/* Skip breakdown */}
            <section className="bg-zinc-900 border border-zinc-800 rounded-xl p-5">
              <h2 className="text-sm font-semibold text-zinc-100 tracking-tight mb-3">Why tickers were skipped</h2>
              {data.skip_breakdown.length === 0 ? (
                <p className="text-xs text-zinc-500">No skips recorded in this run.</p>
              ) : (
                <div className="space-y-2">
                  {data.skip_breakdown.map((s) => (
                    <div key={s.key} className="flex items-center gap-3">
                      <span className="w-44 shrink-0 text-xs text-zinc-400">{s.label}</span>
                      <div className="flex-1 h-5 rounded bg-zinc-800/40 overflow-hidden">
                        <div
                          className="h-full bg-zinc-600 rounded"
                          style={{ width: `${maxSkip ? (s.count / maxSkip) * 100 : 0}%` }}
                        />
                      </div>
                      <span className="w-6 text-right text-xs text-zinc-300 tabular-nums">{s.count}</span>
                    </div>
                  ))}
                </div>
              )}
            </section>

            {/* Near misses */}
            <section className="bg-zinc-900 border border-zinc-800 rounded-xl p-5">
              <div className="mb-3">
                <h2 className="text-sm font-semibold text-zinc-100 tracking-tight">Worth watching</h2>
                <p className="text-xs text-zinc-500 mt-1 leading-relaxed">
                  Skipped on score but within 5 points of the threshold — the likeliest to flip to a buy soon.
                </p>
              </div>
              {data.near_misses.length === 0 ? (
                <p className="text-xs text-zinc-500">No near-misses in this run.</p>
              ) : (
                <div className="flex flex-wrap gap-2">
                  {data.near_misses.map((m) => (
                    <Link
                      key={m.ticker}
                      href={`/stock/${m.ticker}`}
                      className="inline-flex items-center gap-2 rounded-lg bg-amber-950/20 border border-amber-900/30 px-3 py-1.5 hover:bg-amber-950/30 transition-colors"
                    >
                      <span className="text-sm font-semibold text-amber-300">{m.ticker}</span>
                      <span className="text-[11px] text-zinc-500 tabular-nums">
                        {m.score.toFixed(1)} / {m.threshold.toFixed(0)} · −{m.gap.toFixed(1)}
                      </span>
                    </Link>
                  ))}
                </div>
              )}
            </section>
          </>
        )}
      </div>
    </main>
  );
}
