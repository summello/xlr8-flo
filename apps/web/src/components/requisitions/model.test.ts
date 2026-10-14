import { describe, expect, it } from "vitest";
import { EMPTY_HEADER, clientProblems, estimateOf, fundingFor, lineBody, lineTotal, newLine, problemFromError, targetFor } from "./model";

const line = (quantity: string, unit_price: string) => ({ ...newLine(), description: "Pump", quantity, unit_price, uom_code: "ea" });

describe("requisition arithmetic", () => {
  it("3 x 0.10 is 0.30 and 2.5 x 19.99 is 49.98", () => {
    expect(lineTotal(line("3", "0.10"), "USD")).toBe("0.30");
    expect(lineTotal(line("2.5", "19.99"), "USD")).toBe("49.98");
  });
  it("uses the currency exponent: JPY has no decimals", () => {
    expect(lineTotal(line("2.5", "199"), "JPY")).toBe("498");
  });
  it("the estimate equals the sum of the line totals, three ways", () => {
    const lines = [line("3", "0.10"), line("2.5", "19.99")];
    const sum = estimateOf(lines, "USD");
    expect(sum).toBe("50.2800");
    expect(fundingFor({ available: "100.0000", already_reserved: "0.0000" }, sum).thisRequisition).toBe(sum);
    expect(fundingFor({ available: "100.0000", already_reserved: "0.0000" }, sum).availableAfter).toBe("49.7200");
  });
  it("shows a shortfall when the estimate exceeds what is available", () => {
    expect(fundingFor({ available: "40.0000", already_reserved: "0.0000" }, "100.0000")).toMatchObject({ availableAfter: "-60.0000", shortfall: true });
  });
  it("a returned draft that holds its reservation shows no false shortfall (held 60 of 100, available 40)", () => {
    expect(fundingFor({ available: "40.0000", already_reserved: "60.0000" }, "100.0000")).toMatchObject({ availableAfter: "0.0000", shortfall: false });
  });
  it("an incomplete line contributes nothing", () => {
    expect(lineTotal(line("", "5"), "USD")).toBeNull();
    expect(estimateOf([line("x", "5")], "USD")).toBe("0.0000");
  });
});

describe("submission problems", () => {
  it("maps the first path segment to the category and the rest to a control", () => {
    expect(problemFromError({ field: "required/title", message: "Enter a title" })).toEqual({ category: "Required field", message: "Enter a title", target: "req-title" });
    expect(problemFromError({ field: "lines/3/quantity", message: "Line 3 needs a quantity" })).toMatchObject({ category: "Lines", target: "line-3-quantity" });
    expect(problemFromError({ field: "funds/estimate", message: "Over" })).toMatchObject({ category: "Funds", target: "funding-check" });
    expect(problemFromError({ field: "master_data/ledger_account_code", message: "x" })).toMatchObject({ category: "Master data", target: "req-ledger_account_code" });
    expect(targetFor("lines/lines")).toBe("lines-card");
  });
  it("an empty form lists every required problem and each line gap", () => {
    const problems = clientProblems(EMPTY_HEADER, [newLine()], "2026-10-13");
    expect(problems.map(p => p.message)).toEqual([
      "Enter a title", "Choose a project", "Choose a department", "Choose a ledger account", "Enter the date it is needed by",
      "Choose a delivery location", "Explain why it is needed", "Line 1 needs a description", "Line 1 needs a quantity", "Line 1 needs a unit price",
    ]);
    expect(clientProblems(EMPTY_HEADER, [], "2026-10-13").at(-1)?.message).toBe("Add at least one line");
  });
  it("a Project Request must name a sponsor; a requisition does not", () => {
    const request = { ...EMPTY_HEADER, mode: "standalone" as const };
    expect(clientProblems(request, [], "2026-10-13").map(p => p.message)).toContain("Name at least one sponsor");
    expect(clientProblems({ ...request, sponsor_ids: ["u"] }, [], "2026-10-13").map(p => p.message)).not.toContain("Name at least one sponsor");
    expect(clientProblems(EMPTY_HEADER, [], "2026-10-13").map(p => p.message)).not.toContain("Name at least one sponsor");
    expect(targetFor("required/sponsors")).toBe("req-sponsor_ids");
  });
  it("blocks a needed-by date in the past", () => {
    const header = { ...EMPTY_HEADER, needed_by: "2020-01-01" };
    expect(clientProblems(header, [], "2026-10-13").map(p => p.message)).toContain("The needed-by date is in the past");
  });
  it("sends only the item, quantity and price for a catalogue line", () => {
    const body = lineBody({ ...line("1", "5"), catalogue_item_id: "abc" });
    expect(Object.keys(body).sort()).toEqual(["catalogue_item_id", "quantity", "unit_price"]);
  });
});
