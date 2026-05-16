"use client";
import { useState, FormEvent } from "react";

interface Props {
  onAdd: (ticker: string) => void;
  existing: string[];
}

export function AddTickerForm({ onAdd, existing }: Props) {
  const [value, setValue] = useState("");
  const [err, setErr] = useState("");

  function handle(e: FormEvent) {
    e.preventDefault();
    const ticker = value.trim().toUpperCase();
    if (!ticker) return;
    if (existing.includes(ticker)) {
      setErr(`${ticker} is already in your watchlist`);
      return;
    }
    onAdd(ticker);
    setValue("");
    setErr("");
  }

  return (
    <form onSubmit={handle} className="flex gap-2">
      <div className="flex-1">
        <input
          type="text"
          value={value}
          onChange={(e) => { setValue(e.target.value); setErr(""); }}
          placeholder="Add ticker (e.g. NVDA)"
          className="w-full bg-zinc-800 border border-zinc-700 rounded-lg px-3 py-2 text-sm text-white
            placeholder-zinc-500 focus:outline-none focus:border-zinc-500 uppercase"
          maxLength={10}
        />
        {err && <p className="text-xs text-red-400 mt-1">{err}</p>}
      </div>
      <button
        type="submit"
        className="px-4 py-2 bg-zinc-700 hover:bg-zinc-600 text-white text-sm font-medium rounded-lg transition-colors whitespace-nowrap"
      >
        + Add
      </button>
    </form>
  );
}
