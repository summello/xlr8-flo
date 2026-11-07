import { describe, expect, it } from "vitest";
import { chartUnits, currencyExponent, decimal, difference, formatQuantity, isPositiveDecimal, parseQuantity, multiplyQuantize, scaled, share } from "./decimal";
import { formatMoney } from "./money";
describe("exact decimal arithmetic", () => {
  it("preserves 0.1 plus 0.2 and large balances without float conversion", () => {
    expect(decimal(scaled("0.1") + scaled("0.2"))).toBe("0.3000");
    expect(formatMoney(decimal(scaled("0.1") + scaled("0.2")), "USD")).toBe("0.30");
    expect(difference("99999999999999.9999", "0.0001")).toBe("99999999999999.9998");
    expect(difference("0.1", "0.2")).toBe("-0.1000");
  });
  it("scales bars in currency minor units and keeps ratios dimensionless", () => {
    expect(chartUnits("0.1050", "USD")).toBe(11n);
    expect(chartUnits("100.5", "JPY")).toBe(101n);
    expect(share(20n, 100n)).toBe("20.00");
    expect(share(0n, 0n)).toBe("0");
    expect(() => scaled("invalid")).toThrow();
    expect(() => scaled("0.00001")).toThrow();
  });
});

describe("multiplyQuantize", () => {
  it("is exact for cents and rounds half up to the currency exponent", () => {
    expect(multiplyQuantize("3", "0.10", 2)).toBe("0.30");
    expect(multiplyQuantize("2.5", "19.99", 2)).toBe("49.98");
    expect(multiplyQuantize("0.5", "0.01", 2)).toBe("0.01");
    expect(multiplyQuantize("0.4", "0.01", 2)).toBe("0.00");
  });
  it("handles a zero-exponent currency (JPY)", () => {
    expect(currencyExponent("JPY")).toBe(0);
    expect(multiplyQuantize("2.5", "199", currencyExponent("JPY"))).toBe("498");
    expect(multiplyQuantize("1.5", "1", 0)).toBe("2");
  });
  it("accepts only positive server-shaped decimals", () => {
    expect(isPositiveDecimal("0.0001")).toBe(true);
    expect(isPositiveDecimal("0")).toBe(false);
    expect(isPositiveDecimal("1e3")).toBe(false);
    expect(isPositiveDecimal("")).toBe(false);
  });
});

describe("locale quantities", () => {
  it("parses a typed quantity to the canonical string", () => {
    expect(parseQuantity("1,234.5", "en-US")).toBe("1234.5");
    expect(parseQuantity("1.234,5", "de-DE")).toBe("1234.5");
    expect(parseQuantity("1\u202f234,5", "fr-FR")).toBe("1234.5");
  });
  it("refuses what the server would", () => {
    for (const bad of ["0", "1.23456", "abc", "\u0661\u0662", "1".repeat(15), ""]) expect(parseQuantity(bad, "en-US")).toBeNull();
  });
  it("formats without a float", () => {
    expect(formatQuantity("1234.5000", "de-DE")).toBe("1.234,5");
    expect(formatQuantity("1234.5", "en-US")).toBe("1,234.5");
    expect(formatQuantity("99999999999999.0001", "en-US")).toBe("99,999,999,999,999.0001");
    expect(formatQuantity("40.0000", "en-US")).toBe("40");
  });
});
