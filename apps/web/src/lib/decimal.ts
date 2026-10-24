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

/** Digits after the point that a currency keeps; JPY is 0, USD is 2. */
export function currencyExponent(currency: string): number {
  return new Intl.NumberFormat("en-US", { style: "currency", currency }).resolvedOptions().maximumFractionDigits ?? 2;
}

/** quantity x unit price rounded ROUND_HALF_UP to `exponent` places, as kernel.money.quantize does. BigInt only. */
export function multiplyQuantize(quantity: string, unitPrice: string, exponent: number): string {
  const product = scaled(quantity) * scaled(unitPrice); // eight implied decimals
  const sign = product < 0n ? -1n : 1n;
  const magnitude = product < 0n ? -product : product;
  const divisor = 10n ** BigInt(8 - exponent);
  const units = sign * ((magnitude + divisor / 2n) / divisor);
  const digits = (units < 0n ? -units : units).toString().padStart(exponent + 1, "0");
  const text = exponent === 0 ? digits : `${digits.slice(0, -exponent)}.${digits.slice(-exponent)}`;
  return units < 0n ? `-${text}` : text;
}

/** True when the text is a positive decimal the server accepts (14 integer digits, 4 decimals). */
export function isPositiveDecimal(value: string): boolean {
  return /^[0-9]{1,14}(?:\.[0-9]{1,4})?$/.test(value) && /[1-9]/.test(value);
}

const separators = (locale?: string) => {
  const parts = new Intl.NumberFormat(locale).formatToParts(12345.6);
  return { group: parts.find(p => p.type === "group")?.value ?? "", decimal: parts.find(p => p.type === "decimal")?.value ?? "." };
};

/** UX-007: a canonical quantity in the viewer's locale. Digits stay strings; a JS Number would lose them. */
export function formatQuantity(canonical: string, locale?: string): string {
  const [whole = "0", fraction = ""] = canonical.split(".");
  const grouped = new Intl.NumberFormat(locale, { maximumFractionDigits: 0 }).format(BigInt(whole));
  const kept = fraction.replace(/0+$/, "");
  return kept ? grouped + separators(locale).decimal + kept : grouped;
}

/** A typed quantity in the viewer's locale to the canonical string the API takes, or null when it is not a positive quantity. */
export function parseQuantity(input: string, locale?: string): string | null {
  const { group, decimal } = separators(locale);
  let text = input.trim().replace(/\s/gu, "");
  if (group) text = text.split(group.replace(/\s/gu, "")).join("");
  const canonical = text.replace(decimal, ".");
  return isPositiveDecimal(canonical) ? canonical : null;
}
