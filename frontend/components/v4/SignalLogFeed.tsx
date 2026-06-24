"use client";
import { useMemo, useState } from "react";
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
  position_locked:            "Position locked — manual close required",
  trading_paused_all:         "Trading paused — no automated trades",
  trading_paused_entries:     "Trading paused — entries only",
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

// Local-timezone YYYY-MM-DD for a date. Timestamps from the API carry no
// zone suffix and are UTC, so we append "Z" before reading local calendar
// fields — an entry from late-UTC yesterday lands in today's group in Ireland.
function localDateKey(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

function entryDate(ts: string): Date {
  const d = new Date(ts + "Z");
  return isNaN(d.getTime()) ? new Date(ts) : d;
}

// "Tuesday, 24 June 2026" — built from a local-midnight Date so no zone shift.
function formatDateLabel(key: string): string {
  const [y, m, d] = key.split("-").map(Number);
  return new Date(y, m - 1, d).toLocaleDateString("en-GB", {
    weekday: "long",
    day: "numeric",
    month: "long",
    year: "numeric",
  });
}

interface DateGroup {
  key: string;
  label: string;
  entries: SignalLogEntry[];
}

function groupByDate(entries: SignalLogEntry[]): DateGroup[] {
  // Sort newest-first so both the groups and the rows within them descend.
  const sorted = [...entries].sort(
    (a, b) => entryDate(b.timestamp).getTime() - entryDate(a.timestamp).getTime()
  );
  const groups: DateGroup[] = [];
  const byKey = new Map<string, DateGroup>();
  for (const e of sorted) {
    const key = localDateKey(entryDate(e.timestamp));
    let group = byKey.get(key);
    if (!group) {
      group = { key, label: formatDateLabel(key), entries: [] };
      byKey.set(key, group);
      groups.push(group);
    }
    group.entries.push(e);
  }
  return groups;
}

function EntryRow({ e }: { e: SignalLogEntry }) {
  return (
    <div
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
  );
}

function DateSection({
  group,
  open,
  onToggle,
}: {
  group: DateGroup;
  open: boolean;
  onToggle: () => void;
}) {
  const count = group.entries.length;
  return (
    <div>
      <button
        onClick={onToggle}
        aria-expanded={open}
        className="flex w-full items-center gap-2 text-left py-1.5"
      >
        <svg
          width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"
          strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden
          className={`text-zinc-500 transition-transform duration-150 ease-out-quart ${open ? "rotate-90" : ""}`}
        >
          <path d="m9 18 6-6-6-6" />
        </svg>
        <span className="text-sm font-medium text-zinc-200">{group.label}</span>
        <span className="inline-flex px-1.5 py-0.5 rounded-full bg-zinc-800 text-zinc-500 text-[10px] font-medium">
          {count} signal{count === 1 ? "" : "s"}
        </span>
      </button>
      {/* grid-rows 0fr↔1fr gives a CSS-only smooth height transition. */}
      <div
        className={`grid transition-[grid-template-rows] duration-200 ease-out-quart ${
          open ? "grid-rows-[1fr]" : "grid-rows-[0fr]"
        }`}
      >
        <div className="overflow-hidden">
          <div className="space-y-1.5 pl-6 pt-1 pb-1">
            {group.entries.map((e) => (
              <EntryRow key={e.id} e={e} />
            ))}
          </div>
        </div>
      </div>
    </div>
  );
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

  return <GroupedLog entries={entries} />;
}

function GroupedLog({ entries }: { entries: SignalLogEntry[] }) {
  const groups = useMemo(() => groupByDate(entries), [entries]);
  const todayKey = localDateKey(new Date());
  // Explicit toggles only; a key absent from this map falls back to the
  // default (today expanded, every earlier day collapsed).
  const [overrides, setOverrides] = useState<Record<string, boolean>>({});

  return (
    <div className="space-y-1">
      {groups.map((group) => {
        const open = overrides[group.key] ?? group.key === todayKey;
        return (
          <DateSection
            key={group.key}
            group={group}
            open={open}
            onToggle={() =>
              setOverrides((prev) => ({ ...prev, [group.key]: !open }))
            }
          />
        );
      })}
    </div>
  );
}
