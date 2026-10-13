import type { Requisition, RequisitionLine } from "./api";
import { currencyExponent, decimal, difference, isPositiveDecimal, multiplyQuantize, scaled } from "../../lib/decimal";

/** One editable line. Catalogue lines keep their type, description and unit from the item. */
export type DraftLine = {
  key: string;
  line_id?: string;
  line_type: "item" | "service";
  description: string;
  quantity: string;
  uom_code: string;
  unit_price: string;
  catalogue_item_id: string | null;
  category_code: string | null;
};

export type Header = {
  mode: "project" | "standalone";
  title: string;
  project_id: string;
  bu_id: string;
  department_code: string;
  ledger_account_code: string;
  needed_by: string;
  delivery_address_id: string;
  justification: string;
};

export const EMPTY_HEADER: Header = {
  mode: "project", title: "", project_id: "", bu_id: "", department_code: "", ledger_account_code: "",
  needed_by: "", delivery_address_id: "", justification: "",
};

export function newLine(type: "item" | "service" = "item"): DraftLine {
  return { key: crypto.randomUUID(), line_type: type, description: "", quantity: "", uom_code: "", unit_price: "", catalogue_item_id: null, category_code: null };
}

/** Advisory line total in the currency's exponent; null until quantity and price are both valid. */
export function lineTotal(line: Pick<DraftLine, "quantity" | "unit_price">, currency: string): string | null {
  if (!isPositiveDecimal(line.quantity) || !isPositiveDecimal(line.unit_price)) return null;
  return multiplyQuantize(line.quantity, line.unit_price, currencyExponent(currency));
}

export function estimateOf(lines: readonly DraftLine[], currency: string): string {
  return decimal(lines.reduce((sum, line) => sum + scaled(lineTotal(line, currency) ?? "0"), 0n));
}

export type FundingFigures = { available: string; already_reserved: string };
/** available_after = available - (estimate - already_reserved); a held reservation is not counted twice. */
export function fundingFor(funding: FundingFigures, estimate: string) {
  const needed = difference(estimate, funding.already_reserved);
  const after = difference(funding.available, needed);
  return { thisRequisition: estimate, availableAfter: after, shortfall: scaled(needed) > 0n && scaled(after) < 0n };
}

export const CATEGORY_LABELS: Readonly<Record<string, string>> = {
  required: "Required field", master_data: "Master data", project: "Project", lines: "Lines", funds: "Funds", routing: "Routing",
};

export type SummaryProblem = { category: string; message: string; target: string };

const HEADER_FIELDS = new Set(["title", "project_id", "bu_id", "department_code", "ledger_account_code", "needed_by", "delivery_address_id", "justification"]);

/** `lines/3/quantity` -> the control `line-3-quantity`; `required/title` -> `req-title`; the rest land on their card. */
export function targetFor(path: string): string {
  const parts = path.split("/");
  if (parts[0] === "lines" && parts[1] && /^\d+$/.test(parts[1])) return parts[2] ? `line-${parts[1]}-${parts[2]}` : `line-${parts[1]}`;
  if (parts[0] === "lines") return "lines-card";
  if (parts[0] === "funds" || parts[0] === "routing") return "funding-check";
  const field = parts[parts.length - 1] ?? "";
  return HEADER_FIELDS.has(field) ? `req-${field}` : "requisition-details";
}

export function problemFromError(error: { field: string; message: string }): SummaryProblem {
  const slash = error.field.indexOf("/");
  const head = slash < 0 ? error.field : error.field.slice(0, slash);
  const known = head in CATEGORY_LABELS;
  const path = known ? error.field.slice(slash + 1) : error.field;
  const category = known ? head : head === "lines" || /^lines\b/.test(error.field) ? "lines" : HEADER_FIELDS.has(head) ? "required" : "required";
  return { category: CATEGORY_LABELS[category] ?? "Required field", message: error.message, target: targetFor(known ? `${head}/${path}` : error.field) };
}

/** The server repeats every rule; the browser runs the required ones first for speed (requisition-create.md section 6). */
export function clientProblems(header: Header, lines: readonly DraftLine[], today: string): SummaryProblem[] {
  const out: SummaryProblem[] = [];
  const need = (field: string, message: string, category = CATEGORY_LABELS.required!) => out.push({ category, message, target: `req-${field}` });
  if (!header.title.trim()) need("title", "Enter a title");
  if (header.mode === "project") { if (!header.project_id) need("project_id", "Choose a project"); }
  else if (!header.bu_id) need("bu_id", "Choose a business unit");
  if (!header.department_code) need("department_code", "Choose a department");
  if (!header.ledger_account_code) need("ledger_account_code", "Choose a ledger account");
  if (!header.needed_by) need("needed_by", "Enter the date it is needed by");
  else if (header.needed_by < today) need("needed_by", "The needed-by date is in the past");
  if (!header.delivery_address_id) need("delivery_address_id", "Choose a delivery location");
  if (!header.justification.trim()) need("justification", "Explain why it is needed");
  if (lines.length === 0) out.push({ category: CATEGORY_LABELS.lines!, message: "Add at least one line", target: "lines-card" });
  lines.forEach((line, index) => {
    const n = index + 1;
    for (const [field, ok, what] of [["description", line.description.trim() !== "", "a description"], ["quantity", isPositiveDecimal(line.quantity), "a quantity"], ["unit_price", isPositiveDecimal(line.unit_price), "a unit price"]] as const) {
      if (!ok) out.push({ category: CATEGORY_LABELS.lines!, message: `Line ${n} needs ${what}`, target: `line-${n}-${field}` });
    }
  });
  return out;
}

/** The body for PUT .../lines: a catalogue line sends only its item, quantity and price (server rule). */
export function lineBody(line: DraftLine) {
  const base = { ...(line.line_id ? { line_id: line.line_id } : {}), quantity: line.quantity || null, unit_price: line.unit_price || null };
  return line.catalogue_item_id
    ? { ...base, catalogue_item_id: line.catalogue_item_id }
    : { ...base, line_type: line.line_type, description: line.description, uom_code: line.uom_code || null, ...(line.category_code ? { category_code: line.category_code } : {}) };
}

export function localToday(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

export function initialHeader(row: Requisition): Header {
  return {
    mode: row.standalone ? "standalone" : "project", title: row.title, project_id: row.project_id ?? "", bu_id: row.standalone ? row.bu_id : "",
    department_code: row.department_code ?? "", ledger_account_code: row.ledger_account_code ?? "", needed_by: row.needed_by ?? "",
    delivery_address_id: row.delivery_address_id ?? "", justification: row.justification ?? "",
  };
}
export function initialLines(rows: RequisitionLine[]): DraftLine[] {
  return rows.map(l => ({ key: l.id, line_id: l.id, line_type: l.line_type, description: l.description, quantity: l.quantity, uom_code: l.uom_code, unit_price: l.unit_price, catalogue_item_id: l.catalogue_item_id ?? null, category_code: l.category_code }));
}

/** Quantities from the server carry four decimals; show them without trailing zeros. */
export const qty = (v: string) => v.includes(".") ? v.replace(/\.?0+$/, "") || "0" : v;
