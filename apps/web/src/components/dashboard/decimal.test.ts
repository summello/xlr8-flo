import { describe, expect, it } from "vitest";
import { chartUnits, decimal, difference, scaled, share } from "./decimal";
import { formatMoney } from "../../lib/money";
describe("dashboard exact decimal arithmetic", () => {
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
