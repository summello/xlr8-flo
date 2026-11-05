import { describe, expect, it } from "vitest";
import { HOLDS, LIFECYCLE, FUTURE_STATES, TABS, sentSignal } from "./api";

describe("sentSignal", () => {
  it("says the result in words with its date, and a trouble result is a danger or warning tone", () => {
    expect(sentSignal({ result: "delivered", result_at: "2026-10-12T10:00:00Z" })).toEqual({ tone: "success", label: expect.stringMatching(/^Delivered .*12/) });
    expect(sentSignal({ result: "bounced", result_at: null })).toEqual({ tone: "danger", label: "Bounced" });
    expect(sentSignal({ result: "address_suppressed", result_at: null }).tone).toBe("warning");
    expect(sentSignal({ result: "queued", result_at: null })).toEqual({ tone: "info", label: "Queued" });
  });
});

describe("purchase order vocabulary", () => {
  it("has the six server tabs and the three M4 states as future steps", () => {
    expect(TABS.map(t => t[0])).toEqual(["draft", "awaiting", "ready", "issued", "attention", "all"]);
    expect(FUTURE_STATES.every(s => LIFECYCLE.some(([status]) => status === s))).toBe(true);
  });
  it("names what the PO holds in each budget state", () => {
    expect(Object.keys(HOLDS).sort()).toEqual(["committed", "released", "reserved"]);
  });
});
