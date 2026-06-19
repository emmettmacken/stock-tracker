// Human "Updated 2h ago" string from an ISO timestamp.
// Backend timestamps are UTC without a zone suffix, so treat a bare timestamp as UTC.
export function relativeTime(iso: string | null | undefined): string {
  if (!iso) return "never";
  const normalized = /[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : `${iso}Z`;
  const then = new Date(normalized).getTime();
  if (Number.isNaN(then)) return "unknown";

  const secs = Math.max(0, Math.round((Date.now() - then) / 1000));
  if (secs < 60) return "just now";
  const mins = Math.round(secs / 60);
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  return `${days}d ago`;
}
