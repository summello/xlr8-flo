import type { components } from "../../../api/generated/schema";
import type { Po } from "../api";

type Patch = components["schemas"]["PoPatch"];

/** What the form holds: every field as the user sees it (strings, `""` for empty). */
export type Edited = {
  terms: string;
  needed_by: string;
  ship_to_address_id: string | null;
  freight_mode: "line" | "document";
  document_freight: string;
  lines: Record<number, LineEdit>;
};

export type LineEdit = { quantity: string; tax: string; freight: string };
export const BLANK: LineEdit = { quantity: "", tax: "", freight: "" };

export const initial = (po: Po): Edited => ({
  terms: po.terms ?? "",
  needed_by: po.needed_by ?? "",
  ship_to_address_id: po.ship_to_address_id,
  freight_mode: po.freight_mode,
  document_freight: po.document_freight ?? "",
  lines: Object.fromEntries(po.lines.map(l => [l.line_no, { quantity: l.quantity, tax: l.tax, freight: l.freight }])),
});

/** `4`, `4.0` and `4.0000` are one number; compare text, never arithmetic. */
const same = (a: string, b: string) => {
  const norm = (s: string) => { const t = s.trim(); return t.includes(".") ? t.replace(/0+$/, "").replace(/\.$/, "") : t; };
  return norm(a) === norm(b);
};

/** The PATCH body: only what differs from the saved order, and per line only the keys that differ. */
export function patchBody(saved: Po, edited: Edited): Patch {
  const was = initial(saved);
  const body: Patch = {};
  if (edited.terms !== was.terms) body.terms = edited.terms === "" ? null : edited.terms;
  if (edited.needed_by !== was.needed_by) body.needed_by = edited.needed_by === "" ? null : edited.needed_by;
  if (edited.ship_to_address_id !== was.ship_to_address_id) body.ship_to_address_id = edited.ship_to_address_id;
  if (edited.freight_mode !== was.freight_mode) body.freight_mode = edited.freight_mode;
  // also when just switched: a figure typed with the switch must not be dropped
  if (edited.freight_mode === "document" && edited.document_freight.trim() !== "" && !same(edited.document_freight, was.document_freight)) body.document_freight = edited.document_freight;
  const lines: NonNullable<Patch["lines"]> = [];
  for (const l of saved.lines) {
    const before = was.lines[l.line_no] ?? BLANK, now = edited.lines[l.line_no];
    if (!now) continue;
    const line: NonNullable<Patch["lines"]>[number] = { line_no: l.line_no };
    if (!same(now.quantity, before.quantity)) line.quantity = now.quantity;
    if (!same(now.tax, before.tax)) line.tax = now.tax;
    if (edited.freight_mode === "line" && !same(now.freight, before.freight)) line.freight = now.freight;
    if (Object.keys(line).length > 1) lines.push(line);
  }
  if (lines.length > 0) body.lines = lines;
  return body;
}
