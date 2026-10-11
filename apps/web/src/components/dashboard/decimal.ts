/** Exact four-place ledger arithmetic; only dimensionless chart ratios leave BigInt. */
export function scaled(value: string): bigint {
  const match = /^(-?)(\d+)(?:\.(\d{1,4}))?$/.exec(value);
  if (!match) throw new Error("Invalid decimal amount");
  const amount = BigInt(match[2]! + (match[3] ?? "").padEnd(4, "0"));
  return match[1] ? -amount : amount;
}
export function decimal(value: bigint): string {
  const digits = (value < 0n ? -value : value).toString().padStart(5, "0");
  return `${value < 0n ? "-" : ""}${digits.slice(0, -4)}.${digits.slice(-4)}`;
}
export function difference(a: string, b: string): string { return decimal(scaled(a) - scaled(b)); }
export function chartUnits(value: string, currency: string): bigint {
  const exponent = new Intl.NumberFormat("en-US", { style: "currency", currency }).resolvedOptions().maximumFractionDigits ?? 2;
  const amount = scaled(value);
  const sign = amount < 0n ? -1n : 1n;
  const magnitude = amount < 0n ? -amount : amount;
  const divisor = 10n ** BigInt(4 - exponent);
  return sign * ((magnitude + divisor / 2n) / divisor);
}
export function share(value: bigint, maximum: bigint): string {
  return maximum === 0n ? "0" : (value * 10000n / maximum).toString().replace(/(\d{2})$/, ".$1");
}
