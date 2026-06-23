"use client";
import { useCallback, useEffect, useState } from "react";

// Boolean preference backed by localStorage. Reads after mount (not during render) so
// server and first client render agree, avoiding a hydration mismatch.
export function useLocalStorageBool(
  key: string,
  defaultValue = false,
): [boolean, (v: boolean) => void] {
  const [value, setValue] = useState(defaultValue);

  useEffect(() => {
    try {
      const raw = localStorage.getItem(key);
      if (raw != null) setValue(raw === "true");
    } catch { /* localStorage unavailable — keep default */ }
  }, [key]);

  const set = useCallback((v: boolean) => {
    setValue(v);
    try { localStorage.setItem(key, String(v)); } catch { /* ignore */ }
  }, [key]);

  return [value, set];
}
