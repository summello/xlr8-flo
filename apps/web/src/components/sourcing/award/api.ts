import type { components } from "../../../api/generated/schema";
import type { SummaryProblem } from "../../requisitions/model";
import type { Comparison } from "../comparison/api";

export type Award = components["schemas"]["AwardRead"];
export type AwardLine = components["schemas"]["AwardLineRead"];
export type Check = components["schemas"]["RequisitionCheck"];
type Line = Comparison["lines"][number];

/** Zero is tested on the decimal string, never by arithmetic in the browser. */
export const nonZero = (amount: string) => /[1-9]/.test(amount);

/** The one selector behind both verdicts (decision 156): the server's first over-scope check, else its first over-funds one. */
export function verdict(checks: readonly Check[]): { kind: "scope" | "funds" | "ok"; check?: Check } {
  const scope = checks.find(c => nonZero(c.over_scope_by));
  if (scope) return { kind: "scope", check: scope };
  const funds = checks.find(c => nonZero(c.over_funds_by));
  return funds ? { kind: "funds", check: funds } : { kind: "ok" };
}

/** What Set All to Lowest fills: the lowest vendor's quoted quantity, never more than the RFQ line asks for (the API refuses above the quote). */
export const lowestQty = (line: string, quoted: string | null | undefined) => quoted && Number(quoted) < Number(line) ? quoted : line;

export type Row = { quantity: string; reason: string };
export const rowKey = (line: string, vendor: string) => `${line}|${vendor}`;
export const fieldId = (line: string, vendor: string) => `aw-r-${line}-${vendor}`;

/** What a vendor is on a line: the lowest, tied with it (same landed string), or neither. The server decides again at submit. */
export function standing(line: Line, vendor: string): "lowest" | "tie" | "other" {
  if (!line.lowest || line.lowest.vendor_id === vendor) return "lowest";
  const landed = (id: string) => line.cells.find(c => c.vendor_id === id)?.landed;
  return landed(vendor) === landed(line.lowest.vendor_id) ? "tie" : "other";
}

/** The rows to PUT: every positive quantity, its reason in the field the line's standing names. */
export function linesBody(c: Comparison, rows: Record<string, Row>) {
  return c.lines.flatMap(line => line.cells.flatMap(cell => {
    const row = rows[rowKey(line.rfq_line_id, cell.vendor_id)], q = row?.quantity.trim() ?? "";
    if (!row || !nonZero(q)) return [];
    const kind = standing(line, cell.vendor_id), reason = row.reason.trim() || null;
    return [{ rfq_line_id: line.rfq_line_id, vendor_id: cell.vendor_id, quantity: q, justification: kind === "other" ? reason : null, tie_override_reason: kind === "tie" ? reason : null }];
  }));
}

/** Submit's 422 `rationale` / `lines` / `lines/<rfq_line_id>/<vendor_id>` fields, as summary items that focus their control. */
export function problemOf(c: Comparison, e: { field: string; message: string }): SummaryProblem {
  const [head, line, vendor] = e.field.split("/");
  if (head === "rationale") return { category: "Award Rationale", message: e.message, target: "aw-rationale" };
  if (head === "lines" && line && vendor) {
    const no = c.lines.find(l => l.rfq_line_id === line)?.line_no, name = c.vendors.find(v => v.vendor_id === vendor)?.vendor_name;
    return { category: `Line ${no ?? ""} · ${name ?? "Vendor"}`.trim(), message: e.message, target: fieldId(line, vendor) };
  }
  return { category: "Lines", message: e.message, target: "aw-lines" };
}
