// USD formatting helpers shared across the Portfolio page. All monetary values in USD.

export function fmtUSD(n: number, opts: { decimals?: number } = {}): string {
  const { decimals = 2 } = opts;
  return `$${n.toLocaleString("en-US", {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  })}`;
}

// Signed USD, e.g. "+$2,341.00" / "−$120.50" (figure-space minus to match the app).
export function fmtUSDSigned(n: number, opts: { decimals?: number } = {}): string {
  const sign = n >= 0 ? "+" : "−";
  return `${sign}${fmtUSD(Math.abs(n), opts)}`;
}

export function fmtPctSigned(n: number, decimals = 2): string {
  return `${n >= 0 ? "+" : "−"}${Math.abs(n).toFixed(decimals)}%`;
}
