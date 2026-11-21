import { describe, expect, it } from "vitest";
import type { Po, PoChange } from "../api";
import { approvalLine } from "./approval";
import { changeBody, initialEdited } from "./body";

const line = (n: number) => ({ line_no: n, quantity: "4.0000", unit_price: "100.0000", tax: "40.0000", freight: "10.0000" });
const po = (over: Record<string, unknown> = {}) => ({ freight_mode: "line", terms: "Net 30", needed_by: null, document_freight: null, lines: [line(1), line(2)], ...over }) as unknown as Po;
const change = (draft: Record<string, unknown> = {}) => ({ reason: null, draft }) as unknown as PoChange;

describe("changeBody", () => {
  it("sends the PO's own terms and date and an empty lines list when nothing differs", () => {
    const p = po();
    expect(changeBody(p, initialEdited(p, change()))).toEqual({ reason: null, terms: "Net 30", needed_by: null, lines: [] });
  });
  it("one quantity edit is one line with one key; the same decimal written differently is no edit", () => {
    const p = po(), e = initialEdited(p, change());
    e.lines[1]!.quantity = "2";
    e.lines[2]!.unit_price = "100";
    expect(changeBody(p, e).lines).toEqual([{ line_no: 1, quantity: "2" }]);
  });
  it("a reverted edit drops the line, and the last one sends []", () => {
    const p = po(), e = initialEdited(p, change({ lines: { "1": { quantity: "2" } } }));
    expect(changeBody(p, e).lines).toEqual([{ line_no: 1, quantity: "2" }]);
    e.lines[1]!.quantity = "4.0000";
    expect(changeBody(p, e).lines).toEqual([]);
  });
  it("document mode sends document_freight and never a line freight", () => {
    const p = po({ freight_mode: "document", document_freight: "25.0000" }), e = initialEdited(p, change());
    e.documentFreight = "30"; e.lines[1]!.freight = "99";
    expect(changeBody(p, e)).toMatchObject({ document_freight: "30", lines: [] });
    expect(changeBody(po(), initialEdited(po(), change()))).not.toHaveProperty("document_freight");
  });
  it("a cleared number is sent blank, never dropped, for each line field and document freight", () => {
    for (const c of ["quantity", "unit_price", "tax", "freight"] as const) {
      const p = po(), e = initialEdited(p, change({ lines: { "1": { [c]: "1" } } }));
      e.lines[1]![c] = "";
      expect(changeBody(p, e).lines).toEqual([{ line_no: 1, [c]: "" }]);
    }
    const p = po({ freight_mode: "document", document_freight: "25.0000" }), e = initialEdited(p, change({ document_freight: "30" }));
    e.documentFreight = "";
    expect(changeBody(p, e)).toMatchObject({ document_freight: "" });
    const base = po({ freight_mode: "document" }), saved = initialEdited(base, change({ document_freight: "30" }));
    saved.documentFreight = "";
    expect(changeBody(base, saved)).toMatchObject({ document_freight: "" }); // null on the PO, a value in the saved draft
    const none = po({ freight_mode: "document" });
    expect(changeBody(none, initialEdited(none, change()))).not.toHaveProperty("document_freight");
  });
  it("clearing the date sends null", () => {
    const p = po({ needed_by: "2026-11-01" }), e = initialEdited(p, change());
    e.neededBy = "";
    expect(changeBody(p, e).needed_by).toBeNull();
  });
});

describe("approvalLine", () => {
  const d = (...fields: string[]) => fields.map(field => ({ field }));
  it("says each sentence from the server's material and the diff's fields", () => {
    expect(approvalLine({ material: false, diff: [] })).toBe("No changes yet.");
    expect(approvalLine({ material: true, diff: d("quantity") })).toBe("Needs approval: the amount changes");
    expect(approvalLine({ material: true, diff: d("tax", "line") })).toBe("Needs approval: the amount changes and the line count changes");
    expect(approvalLine({ material: true, diff: d("terms") })).toBe("Needs approval.");
    expect(approvalLine({ material: false, diff: d("needed_by") })).toBe("No approval needed: only the needed-by date changes");
    expect(approvalLine({ material: false, diff: d("terms") })).toBe("No approval needed: only the terms change");
    expect(approvalLine({ material: false, diff: d("needed_by", "terms") })).toBe("No approval needed: only the needed-by date and the terms change");
    expect(approvalLine({ material: false, diff: d("quantity") })).toBe("No approval needed.");
  });
  it("trusts material over the fields", () => {
    expect(approvalLine({ material: true, diff: d("needed_by") })).toBe("Needs approval.");
    expect(approvalLine({ material: false, diff: d("tax") })).toBe("No approval needed.");
  });
});
