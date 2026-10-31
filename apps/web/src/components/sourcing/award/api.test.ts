import { describe, expect, it } from "vitest";
import { lowestQty, nonZero, problemOf, standing, verdict, type Check } from "./api";

const check = (over: Partial<Check>): Check => ({ requisition_id: "r", requisition: "REQ-1", scope: "100.00", funds: "100.00", award: "80.00", over_scope_by: "0.00", over_funds_by: "0.00", release: "20.00", main_project: "PRJ-1", ...over });

describe("verdict", () => {
  it("reads the server's figures: scope first, then funds, else within", () => {
    expect(verdict([check({}), check({ requisition: "REQ-2", over_funds_by: "5.00" }), check({ requisition: "REQ-3", over_scope_by: "1.00" })]).check?.requisition).toBe("REQ-3");
    expect(verdict([check({ over_funds_by: "5.00" })]).kind).toBe("funds");
    expect(verdict([check({})]).kind).toBe("ok");
  });
  it("trusts the server's over figure even where the lines disagree", () => {
    expect(verdict([check({ award: "50.00", over_scope_by: "7.00" })]).kind).toBe("scope");
    expect(verdict([check({ award: "500.00", over_scope_by: "0.00" })]).kind).toBe("ok");
  });
  it("tests zero on the string", () => { expect(["0", "0.0000", "00.00"].some(nonZero)).toBe(false); expect(nonZero("0.0001")).toBe(true); });
});

describe("lowestQty", () => {
  it("is the quote when partial, else the line quantity", () => {
    expect(lowestQty("4.0000", "2.0000")).toBe("2.0000");
    expect(lowestQty("4.0000", "6.0000")).toBe("4.0000");
    expect(lowestQty("4.0000", null)).toBe("4.0000");
  });
});

describe("standing and problems", () => {
  const line = { rfq_line_id: "l1", line_no: 2, lowest: { vendor_id: "a", tied: true }, cells: [{ vendor_id: "a", landed: "10.0000" }, { vendor_id: "b", landed: "10.0000" }, { vendor_id: "c", landed: "12.0000" }] } as never;
  it("names lowest, tie and other", () => { expect(["a", "b", "c"].map(v => standing(line, v))).toEqual(["lowest", "tie", "other"]); });
  it("maps a field to its control", () => {
    const c = { lines: [line], vendors: [{ vendor_id: "b", vendor_name: "Bolt" }] } as never;
    expect(problemOf(c, { field: "lines/l1/b", message: "m" })).toEqual({ category: "Line 2 · Bolt", message: "m", target: "aw-r-l1-b" });
    expect(problemOf(c, { field: "rationale", message: "m" }).target).toBe("aw-rationale");
  });
});
