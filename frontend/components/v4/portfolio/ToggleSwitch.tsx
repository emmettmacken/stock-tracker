"use client";

// Generic slider switch. `onColor`/`offColor` are Tailwind bg-* classes so callers can
// signal meaning (e.g. red when "off" means paused, vs neutral zinc for a plain pref).
export function ToggleSwitch({
  checked,
  disabled,
  onChange,
  onColor = "bg-emerald-500",
  offColor = "bg-zinc-600",
  label,
}: {
  checked: boolean;
  disabled?: boolean;
  onChange: (next: boolean) => void;
  onColor?: string;
  offColor?: string;
  label?: string;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={`relative inline-flex h-5 w-9 shrink-0 items-center rounded-full transition-colors duration-150 ease-out-quart focus:outline-none focus:ring-1 focus:ring-zinc-500 disabled:opacity-40 disabled:cursor-not-allowed ${
        checked ? onColor : offColor
      }`}
    >
      <span
        className={`inline-block h-3.5 w-3.5 transform rounded-full bg-white shadow transition-transform duration-150 ease-out-quart ${
          checked ? "translate-x-[18px]" : "translate-x-1"
        }`}
      />
    </button>
  );
}
