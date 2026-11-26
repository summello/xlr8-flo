import { describe, expect, it } from "vitest";
import type { Po } from "../api";
import { initial, patchBody } from "./patch";

const line = (n: number) => ({ line_no: n, quantity: "4.0000", tax: "40.0000", freight: "10.0000" });
const po = { terms: "Net 30", needed_by: "2026-11-01", ship_to_address_id: null, freight_mode: "line", document_freight: null, lines: [line(1), line(2)] } as unknown as Po;

describe("patchBody", () => {
  it("sends nothing when nothing changed", () => expect(patchBody(po, initial(po))).toEqual({}));
  it("sends one line with one key for one tax edit", () => {
    const e = initial(po);
    e.lines[1] = { ...e.lines[1]!, tax: "41.50" };
    expect(patchBody(po, e)).toEqual({ lines: [{ line_no: 1, tax: "41.50" }] });
  });
  it("treats 4 and 4.0000 as the same quantity", () => {
    const e = initial(po);
    e.lines[2] = { ...e.lines[2]!, quantity: "4" };
    expect(patchBody(po, e)).toEqual({});
  });
  it("sends null for a cleared needed-by date", () => {
    const e = initial(po);
    e.needed_by = "";
    expect(patchBody(po, e)).toEqual({ needed_by: null });
  });
  it("sends only the mode when switching to document freight", () => {
    const e = initial(po);
    e.freight_mode = "document";
    expect(patchBody(po, e)).toEqual({ freight_mode: "document" });
  });
  it("keeps document freight typed together with the switch to document mode", () => {
    const e = initial(po);
    e.freight_mode = "document";
    e.document_freight = "9.99";
    expect(patchBody(po, e)).toEqual({ freight_mode: "document", document_freight: "9.99" });
  });
});
