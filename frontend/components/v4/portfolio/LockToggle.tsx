"use client";

// Slider switch for the position lock. Green track = unlocked (automated exits active),
// red track = locked (automated exits skipped). Locking is gated by a confirmation
// modal in the parent; unlocking happens immediately.
export function LockToggle({
  locked,
  disabled,
  onToggle,
}: {
  locked: boolean;
  disabled?: boolean;
  onToggle: () => void;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={locked}
      aria-label={locked ? "Position locked — click to unlock" : "Position unlocked — click to lock"}
      title={locked ? "Locked — automated exits disabled" : "Unlocked — automated exits active"}
      disabled={disabled}
      onClick={onToggle}
      className={`relative inline-flex h-5 w-9 shrink-0 items-center rounded-full transition-colors duration-150 ease-out-quart focus:outline-none focus:ring-1 focus:ring-zinc-500 disabled:opacity-40 disabled:cursor-not-allowed ${
        locked ? "bg-red-500" : "bg-emerald-500"
      }`}
    >
      <span
        className={`inline-block h-3.5 w-3.5 transform rounded-full bg-white shadow transition-transform duration-150 ease-out-quart ${
          locked ? "translate-x-[18px]" : "translate-x-1"
        }`}
      />
    </button>
  );
}
