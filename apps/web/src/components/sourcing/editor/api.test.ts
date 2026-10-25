import { describe, expect, it } from "vitest";
import type { Rfq } from "../api";
import { build, changes, formOf, summaryOf } from "./api";

const rfq = {
  id: "r", status: "published", deadline_at: "2030-01-01T10:00:00Z", deadline_tz: "UTC", specifications: "Pump spec", delivery_location: "Dock 4", delivery_terms: "DAP", terms: "Net 30",
  lines: [{ id: "l1", line_no: 1, requisition_line_id: "q1", requisition_version: 3, quantity: "4.0000", specification: null }],
} as unknown as Rfq;

describe("What Changed", () => {
  it("is empty for an untouched form and lists exactly the edited fields with their old values", () => {
    const form = formOf(rfq);
    expect(changes(rfq, form)).toEqual([]);
    const edited = { ...form, terms: "Net 45", time: "11:00", qty: { q1: "6" }, lineSpecs: { l1: "Stainless" } };
    expect(changes(rfq, edited).map(c => [c.label, c.was, c.now])).toEqual([
      ["Terms", "Net 30", "Net 45"],
      ["Response Deadline", "2030-01-01 10:00 UTC", "2030-01-01 11:00 UTC"],
      ["Line 1 specification", "none", "Stainless"],
      ["Line 1 quantity", "4", "6"],
    ]);
  });
  it("counts a cleared field as a change to none", () => {
    expect(changes(rfq, { ...formOf(rfq), terms: "  " }).map(c => [c.label, c.was, c.now])).toEqual([["Terms", "Net 30", "none"]]);
  });
});

describe("the request body", () => {
  it("sends only what changed on an amendment, all four texts on a draft save, and blank as null", () => {
    const form = { ...formOf(rfq), terms: "" };
    expect(build(rfq, form, "amend").body).toEqual({ terms: null });
    expect(Object.keys(build(rfq, form, "edit").body).sort()).toEqual(["deadline_at", "deadline_tz", "delivery_location", "delivery_terms", "specifications", "terms"]);
  });
  it("sends every line when a quantity changed, and refuses a malformed deadline or quantity before sending", () => {
    const body = build(rfq, { ...formOf(rfq), qty: { q1: "6" } }, "amend").body;
    expect(body.lines).toEqual([{ requisition_line_id: "q1", requisition_version: 3, quantity: "6" }]);
    expect(build(rfq, { ...formOf(rfq), date: "2030-13-01" }, "amend").errors.map(e => e.fieldId)).toEqual(["rfq-date"]);
    expect(build(rfq, { ...formOf(rfq), qty: { q1: "abc" } }, "amend").errors.map(e => e.fieldId)).toEqual(["rfq-qty-q1"]);
  });
});

describe("server problems", () => {
  it("renders every errors[] entry by its message, mapped to a field where it knows one", () => {
    const rows = summaryOf({ errors: [{ field: "terms", message: "Add the terms." }, { field: "weights_total", message: "Weights must total 100." }, { field: "invitees/abc", message: "V has no contact." }] } as never, "Publishing");
    expect(rows).toEqual([
      { fieldId: "rfq-terms", label: "Terms", message: "Add the terms." },
      { fieldId: "rfq-form", label: "RFQ", message: "Weights must total 100." },
      { fieldId: "rfq-invitees", label: "Invited Vendors", message: "V has no contact." },
    ]);
  });
});
