"use client";
import { SignalLogEntry } from "@/lib/types";
import { SignalBadge } from "@/components/SignalBadge";
import { Skeleton } from "@/components/v3/Skeleton";

function ActionPill({ action }: { action: string }) {
  const styles: Record<string, string> = {
    ordered: "bg-emerald-900/50 text-emerald-300 border-emerald-700/40",
    closed:  "bg-sky-900/50 text-sky-300 border-sky-700/40",
    skipped: "bg-zinc-800 text-zinc-500 border-zinc-700/40",
  };
  const cls = styles[action] ?? styles.skipped;
  return (
    <span className={`inline-flex px-1.5 py-0.5 rounded border text-[10px] font-medium capitalize ${cls}`}>
      {action}
    </span>
  );
}

const SKIP_LABELS: Record<string, string> = {
  already_in_position:        "Already in position",
  earnings_within_2d:         "Earnings within 2 days",
  data_unavailable:           "Data unavailable",
  bull_prob_below_threshold:  "Bull probability too low",
  score_below_threshold:      "Score below threshold",
  sentiment_too_low:          "Sentiment too low",
  volume_below_average:       "Volume below average",
  overextended:               "Price >25% above MA20",
  sector_concentration:       "Sector limit (max 3)",
  score_deterioration:        "Score fell below 40",
  momentum_disagreement:      "3m/12m momentum disagree",
  reentry_cooldown:           "Re-entry cooldown (2d)",
  macro_drawdown_protection:  "Macro drawdown protection",
  min_factor_floor:           "Factor floor (score capped)",
  // Retained for historical rows logged before the gate was renamed to
  // bull_prob_below_threshold; no new rows carry this reason.
  hmm_not_buy_transition:     "Bull probability too low (historical)",
};

function formatSkipReason(reason: string): string {
  if (reason.startsWith("vix_too_high:")) return `VIX too high (${reason.split(":")[1]})`;
  if (reason.startsWith("order_failed:")) return "Order failed";
  if (reason.startsWith("close_failed:")) return "Close failed";
  // Several reasons are logged with a ":value" suffix (e.g. "score_below_threshold:62<63");
  // look up the label by the key part before the colon.
  const key = reason.split(":")[0];
  return SKIP_LABELS[key] ?? reason.replace(/_/g, " ");
}

function relTime(ts: string) {
  try {
    const diff = (Date.now() - new Date(ts + "Z").getTime()) / 1000;
    if (diff < 60) return "just now";
    if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
    if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
    return new Date(ts + "Z").toLocaleDateString();
  } catch {
    return ts.slice(0, 10);
  }
}

interface Props {
  entries: SignalLogEntry[] | null;
  loading: boolean;
  error: string | null;
  onRetry: () => void;
}

export function SignalLogFeed({ entries, loading, error, onRetry }: Props) {
  if (loading) return (
    <div className="space-y-2">
      {[1, 2, 3, 4].map((i) => <Skeleton key={i} className="h-12 w-full" />)}
    </div>
  );

  if (error) return (
    <div className="text-red-400 text-xs py-4">
      {error} <button onClick={onRetry} className="ml-2 underline text-zinc-400 hover:text-white">Retry</button>
    </div>
  );

  if (!entries?.length) return (
    <div className="text-zinc-600 text-sm py-8 text-center">
      No signals logged yet.<br />
      <span className="text-xs text-zinc-700">Use &ldquo;Run signals now&rdquo; to generate the first batch.</span>
    </div>
  );

  return (
    <div className="space-y-1.5">
      {entries.map((e) => (
        <div
          key={e.id}
          className="flex items-start gap-3 bg-zinc-900 border border-zinc-800/60 rounded-lg px-3 py-2.5 text-xs
            hover:border-zinc-700/80 transition-colors duration-150 ease-out-quart"
        >
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="font-bold text-zinc-200">{e.ticker}</span>
              {e.signal && <SignalBadge signal={e.signal as "BUY" | "SELL" | "HOLD"} />}
              <ActionPill action={e.action} />
              {e.composite_score != null && (
                <span className="text-zinc-500 tabular-nums">score {e.composite_score.toFixed(1)}</span>
              )}
            </div>
            {e.skip_reason && (
              <div className="text-zinc-600 text-[10px] mt-0.5 truncate">
                {formatSkipReason(e.skip_reason)}
              </div>
            )}
          </div>
          <div className="text-zinc-600 text-[10px] shrink-0 text-right">
            <div>{relTime(e.timestamp)}</div>
            {e.price_at_signal != null && (
              <div className="text-zinc-700">${e.price_at_signal.toFixed(2)}</div>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}
