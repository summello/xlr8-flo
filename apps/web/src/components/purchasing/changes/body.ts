import type { components } from "../../../api/generated/schema";
import type { Po, PoChange } from "../api";

type Patch = components["schemas"]["PoChangePatch"];
type LineEdit = components["schemas"]["PoChangeLineEdit"];

export const COLUMNS = ["quantity", "unit_price", "tax", "freight"] as const;
export type Column = (typeof COLUMNS)[number];
/** What the form holds: every input as typed text, so nothing the user typed is ever rewritten. */
export type Edited = { reason: string; terms: string; neededBy: string; documentFreight: string; /** a document freight value is on the PO, in the saved draft, or was typed: a blank must then be sent to clear it */ freightSet: boolean; lines: Record<number, Record<Column, string>> };

/** "4" and "4.0000" are the same decimal; compared as text, so no float touches a money string. */
const same = (a: string, b: string) => {
  const norm = (v: string) => { const t = v.trim(); return t.includes(".") ? t.replace(/0+$/, "").replace(/\.$/, "") : t; };
  return norm(a) === norm(b);
};

/** The form's starting values: the draft's where it has one, else the revision's frozen ones. */
export function initialEdited(po: Po, change: PoChange): Edited {
  const draft = change.draft as { terms?: string | null; needed_by?: string | null; document_freight?: string; lines?: Record<string, Partial<Record<Column, string>>> };
  return {
    reason: change.reason ?? "",
    terms: ("terms" in draft ? draft.terms : po.terms) ?? "",
    neededBy: ("needed_by" in draft ? draft.needed_by : po.needed_by) ?? "",
    documentFreight: draft.document_freight ?? po.document_freight ?? "",
    freightSet: draft.document_freight !== undefined || po.document_freight !== null,
    lines: Object.fromEntries(po.lines.map(l => {
      const d = draft.lines?.[String(l.line_no)] ?? {};
      return [l.line_no, Object.fromEntries(COLUMNS.map(c => [c, d[c] ?? l[c]])) as Record<Column, string>];
    })),
  };
}

/** The complete desired draft. `lines` is always sent (`[]` clears a reverted edit: a field left out of a PATCH is unchanged). */
export function changeBody(po: Po, e: Edited): Patch {
  const lines: LineEdit[] = [];
  for (const l of po.lines) {
    const edit: LineEdit = { line_no: l.line_no };
    for (const c of COLUMNS) {
      const v = e.lines[l.line_no]?.[c];
      if (v === undefined || (c === "freight" && po.freight_mode === "document")) continue;
      // A blank is sent as typed so the server refuses it at the boundary; it is never dropped.
      if (!same(v, l[c])) edit[c] = v.trim();
    }
    if (Object.keys(edit).length > 1) lines.push(edit);
  }
  return {
    reason: e.reason || null, terms: e.terms || null, needed_by: e.neededBy || null, lines,
    // Blank is sent as typed (the server refuses it) whenever a value exists to clear; with none anywhere there is nothing to send.
    ...(po.freight_mode === "document" && (e.documentFreight.trim() || e.freightSet) ? { document_freight: e.documentFreight.trim() } : {}),
  };
}
