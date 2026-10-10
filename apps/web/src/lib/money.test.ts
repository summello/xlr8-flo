import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import Money from "../components/ui/Money";
import { describe, expect, it } from "vitest";
import { consumptionPercent, formatMoney } from "./money";

describe("decimal money", () => {
  it.each([
    ["0.1", "USD", "0.10"], ["0.2", "USD", "0.20"],
    ["0.30000000000000000001", "USD", "0.30"],
    ["-0.005", "USD", "(-0.01)"], ["1234.5", "JPY", "1,235"],
    ["12345678901234567890.12", "USD", "12,345,678,901,234,567,890.12"],
    ["-1234.5", "USD", "(-1,234.50)"], ["0", "USD", "0.00"],
    ["0.1049999999999999999", "USD", "0.10"],
  ])("formats %s %s exactly", (value, currency, expected) => {
    expect(formatMoney(value, currency)).toBe(expected);
  });
  it("computes consumption with decimal strings", () => {
    expect(consumptionPercent("0.3000", "0.1000")).toBe("66.67%");
    expect(consumptionPercent("0", "0")).toBe("—");
    expect(consumptionPercent("100.0000", "110.0000")).toBe("-10.00%");
  });
  it("rejects invalid amount strings", () => expect(() => formatMoney("bad", "USD")).toThrow("Invalid decimal amount"));
});


it("distinguishes zero from a balance the caller cannot access", () => {
  const zero = renderToStaticMarkup(createElement(Money, { value: "0.0000", currency: "USD" }));
  expect(zero).toContain('data-zero="true"');
  expect(zero).toContain("—");
  expect(zero).toContain("USD");
  expect(zero).not.toContain("No access to balances");
  const denied = renderToStaticMarkup(createElement(Money, { value: null, currency: "USD" }));
  expect(denied).toContain("No access to balances");
});
