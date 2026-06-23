"use client";
import { useCallback, useEffect, useState } from "react";
import {
  TradingSettings, fetchTradingSettings, updateTradingSettings,
} from "./api";

// Shared automated-trading state for the Portfolio settings modal and the Automation
// page. Each consumer holds its own instance; we refetch on window focus so a change
// made in one place is reflected in the other when the user returns to it.
export function useTradingSettings() {
  const [settings, setSettings] = useState<TradingSettings | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(() => {
    setError(null);
    return fetchTradingSettings()
      .then(setSettings)
      .catch((e) => setError(e instanceof Error ? e.message : "Failed to load settings"))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    reload();
    const onFocus = () => reload();
    window.addEventListener("focus", onFocus);
    return () => window.removeEventListener("focus", onFocus);
  }, [reload]);

  // Optimistic update: apply the patch locally, POST it, and revert on error. Returns
  // the promise so callers can surface failures (the throw is swallowed after revert).
  const update = useCallback(
    async (patch: Partial<TradingSettings>) => {
      const prev = settings;
      if (prev) setSettings({ ...prev, ...patch });
      try {
        const next = await updateTradingSettings(patch);
        setSettings(next);
        return next;
      } catch (e) {
        if (prev) setSettings(prev); // revert
        setError(e instanceof Error ? e.message : "Failed to update settings");
        throw e;
      }
    },
    [settings],
  );

  return { settings, loading, error, reload, update };
}
