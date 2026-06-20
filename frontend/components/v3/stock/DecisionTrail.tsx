"use client";
import { DecisionTrail } from "@/lib/types";

// Display-only rendering of the gate-by-gate evaluation reconstructed from signal_log.
// Mirrors the real order gates are checked in _run_signal_job(); no logic runs here.

function CheckIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M20 6 9 17l-5-5" />
    </svg>
  );
}

function XIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M18 6 6 18M6 6l12 12" />
    </svg>
  );
}

function OrderIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M12 2v20M2 12h20" />
    </svg>
  );
}

export function EligibilityBanner({ trail }: { trail: DecisionTrail | null }) {
  if (!trail) return null;

  if (trail.outcome === "no_data" || trail.outcome === "exit_only" || trail.outcome === "other") {
    return (
      <div className="rounded-xl border border-zinc-800 bg-zinc-900 px-4 py-3 flex items-center gap-3">
        <span className="inline-block h-2 w-2 rounded-full bg-zinc-600" aria-hidden />
        <span className="text-sm text-zinc-400">{trail.summary}</span>
      </div>
    );
  }

  const yes = trail.would_trade_today;
  return (
    <div
      className={`rounded-xl border px-4 py-3 flex items-start gap-3 ${
        yes
          ? "border-emerald-700/50 bg-emerald-950/30"
          : "border-zinc-800 bg-zinc-900"
      }`}
    >
      <span
        className={`mt-0.5 inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-full ${
          yes ? "bg-emerald-500/15 text-emerald-400" : "bg-zinc-800 text-zinc-400"
        }`}
      >
        {yes ? <CheckIcon /> : <XIcon />}
      </span>
      <div className="min-w-0">
        <p className={`text-sm font-medium ${yes ? "text-emerald-300" : "text-zinc-200"}`}>
          Would trade today: {yes ? "Yes" : "No"}
        </p>
        <p className="text-xs text-zinc-500 mt-0.5 leading-relaxed">{trail.summary}</p>
      </div>
    </div>
  );
}

export function DecisionTrailList({ trail }: { trail: DecisionTrail | null }) {
  if (!trail) return null;
  if (!trail.gates.length) {
    return (
      <p className="text-xs text-zinc-500">
        {trail.summary}
        {trail.evaluated_at && (
          <span className="text-zinc-600"> · {new Date(trail.evaluated_at).toLocaleString()}</span>
        )}
      </p>
    );
  }

  return (
    <ol className="space-y-1.5">
      {trail.gates.map((g) => {
        const failed = g.status === "failed";
        const ordered = g.status === "ordered";
        const iconWrap = failed
          ? "bg-red-500/15 text-red-400"
          : ordered
          ? "bg-emerald-500/15 text-emerald-400"
          : "bg-emerald-500/10 text-emerald-500";
        return (
          <li
            key={g.key}
            className={`flex items-start gap-3 rounded-lg px-3 py-2 ${
              failed ? "bg-red-950/20 border border-red-900/30" : ordered ? "bg-emerald-950/20 border border-emerald-900/30" : "bg-zinc-800/30"
            }`}
          >
            <span className={`mt-0.5 inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-full ${iconWrap}`}>
              {ordered ? <OrderIcon /> : failed ? <XIcon /> : <CheckIcon />}
            </span>
            <div className="min-w-0 flex-1">
              <p className={`text-sm ${failed ? "text-red-300 font-medium" : ordered ? "text-emerald-300 font-medium" : "text-zinc-300"}`}>
                {g.label}
              </p>
              {g.detail && <p className="text-xs text-zinc-500 mt-0.5 leading-relaxed">{g.detail}</p>}
            </div>
          </li>
        );
      })}
    </ol>
  );
}
