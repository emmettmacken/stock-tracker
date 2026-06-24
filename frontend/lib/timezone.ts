"use client";
import { useCallback, useEffect, useMemo, useState } from "react";

// Timezone selector for the Automation page. Scheduled job times are fixed
// wall-clock times in US Eastern (the market's timezone); this converts them to
// whatever timezone the user picks for display. Pure display — no backend change.

const STORAGE_KEY = "automation-timezone";
export const AUTO = "auto";

// IANA zone the ET schedules are anchored to. Handles EST/EDT automatically.
const ET_ZONE = "America/New_York";

export type TimezoneOption = { value: string; label: string };

// `value === AUTO` resolves to the browser's timezone at render time.
export const TIMEZONE_OPTIONS: TimezoneOption[] = [
  { value: AUTO, label: "Auto (browser timezone)" },
  { value: "Europe/Dublin", label: "Europe/Dublin (IST/GMT)" },
  { value: "America/New_York", label: "America/New_York (ET)" },
  { value: "America/Chicago", label: "America/Chicago (CT)" },
  { value: "America/Los_Angeles", label: "America/Los_Angeles (PT)" },
  { value: "Europe/London", label: "Europe/London (GMT/BST)" },
  { value: "Asia/Tokyo", label: "Asia/Tokyo (JST)" },
];

// Minutes the given timezone is ahead of UTC at `date` (negative = behind UTC).
function tzOffsetMinutes(date: Date, timeZone: string): number {
  const dtf = new Intl.DateTimeFormat("en-US", {
    timeZone,
    hour12: false,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
  const parts: Record<string, number> = {};
  for (const p of dtf.formatToParts(date)) {
    if (p.type !== "literal") parts[p.type] = Number(p.value);
  }
  // `hour` can come back as 24 at midnight in some engines — normalise.
  const hour = parts.hour === 24 ? 0 : parts.hour;
  const asUTC = Date.UTC(parts.year, parts.month - 1, parts.day, hour, parts.minute, parts.second);
  return (asUTC - date.getTime()) / 60000;
}

// The UTC instant for today's ET wall-clock time (e.g. 15:30 ET), accounting for
// DST. Anchored to "today" in ET so the converted times track the current offset.
function etTimeTodayInstant(hour: number, minute: number): Date {
  const now = new Date();
  const etDate = new Intl.DateTimeFormat("en-CA", {
    timeZone: ET_ZONE,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(now); // YYYY-MM-DD in ET
  const [y, m, d] = etDate.split("-").map(Number);
  const naiveUTC = Date.UTC(y, m - 1, d, hour, minute);
  const offset = tzOffsetMinutes(new Date(naiveUTC), ET_ZONE);
  return new Date(naiveUTC - offset * 60000);
}

// "20:30 IST" — a fixed daily ET schedule rendered in the target timezone.
export function formatEtScheduledTime(hour: number, minute: number, timeZone: string): string {
  return new Intl.DateTimeFormat("en-GB", {
    timeZone,
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
    timeZoneName: "short",
  }).format(etTimeTodayInstant(hour, minute));
}

// "24/06/2026 20:45" — an absolute UTC timestamp rendered in the target timezone.
export function formatTimestamp(date: Date | string | number, timeZone: string): string {
  return new Intl.DateTimeFormat("en-GB", {
    timeZone,
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date(date));
}

// Selected timezone, persisted in localStorage. `choice` may be AUTO; `timeZone`
// is the resolved IANA zone. `mounted` is false until the localStorage / browser
// timezone has been read on the client, so callers can avoid hydration mismatch.
export function useTimezone() {
  const [choice, setChoiceState] = useState<string>(AUTO);
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (raw) setChoiceState(raw);
    } catch {
      /* localStorage unavailable — keep default */
    }
  }, []);

  const setChoice = useCallback((v: string) => {
    setChoiceState(v);
    try {
      localStorage.setItem(STORAGE_KEY, v);
    } catch {
      /* ignore */
    }
  }, []);

  const timeZone = useMemo(() => {
    if (choice !== AUTO) return choice;
    try {
      return Intl.DateTimeFormat().resolvedOptions().timeZone || ET_ZONE;
    } catch {
      return ET_ZONE;
    }
  }, [choice]);

  return { choice, setChoice, timeZone, mounted };
}
