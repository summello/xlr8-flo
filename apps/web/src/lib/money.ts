/** Decimal strings stay exact through rounding, including values beyond IEEE-754. */
export function formatMoney(value: string, currency: string, locale = "en-US"): string {
  const match = /^(-?)(\d+)(?:\.(\d+))?$/.exec(value);
  if (!match) throw new Error("Invalid decimal amount");
  const exponent = new Intl.NumberFormat(locale, { style: "currency", currency })
    .resolvedOptions().maximumFractionDigits ?? 2;
  const fraction = match[3] ?? "";
  let units = BigInt(match[2]! + fraction.padEnd(exponent, "0").slice(0, exponent));
  if ((fraction[exponent] ?? "0") >= "5") units += 1n;
  const digits = units.toString().padStart(exponent + 1, "0");
  const integer = exponent ? digits.slice(0, -exponent) : digits;
  const grouped = new Intl.NumberFormat(locale, { maximumFractionDigits: 0 }).format(BigInt(integer));
  const separator = new Intl.NumberFormat(locale).formatToParts(1.1).find(p => p.type === "decimal")?.value ?? ".";
  const result = grouped + (exponent ? separator + digits.slice(-exponent) : "");
  return match[1] ? `(-${result})` : result;
}

/** Consumption is (allocated - available) / allocated, rounded half-up to 2 places. */
export function consumptionPercent(allocated: string, available: string): string {
  const scaled = (value: string) => {
    const [whole, fraction = ""] = value.split(".");
    const negative = whole!.startsWith("-");
    const digits = BigInt(whole!.replace("-", "") + fraction.padEnd(4, "0"));
    return negative ? -digits : digits;
  };
  const total = scaled(allocated);
  if (total === 0n) return "—";
  const consumed = (total - scaled(available)) * 10000n;
  const negative = (consumed < 0n) !== (total < 0n);
  const numerator = consumed < 0n ? -consumed : consumed;
  const denominator = total < 0n ? -total : total;
  const rounded = (numerator * 2n + denominator) / (denominator * 2n);
  const digits = rounded.toString().padStart(3, "0");
  return `${negative ? "-" : ""}${digits.slice(0, -2)}.${digits.slice(-2)}%`;
}
