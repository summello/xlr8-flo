import { useId, type ReactNode } from "react";
import { apiClient } from "../../../api/client";
import Eligibility from "../../vendors/Eligibility";
import Field from "../../form/Field";
import Input from "../../form/Input";
import MoneyInput from "../../form/MoneyInput";
import Select from "../../form/Select";
import { FieldContext } from "../../form/context";
import { failure, type Problem } from "../../requisitions/api";
import { useLoad } from "../../requisitions/useLoad";
import Card from "../../sourcing/Card";
import Money from "../../ui/Money";
import Person from "../../ui/Person";
import type { components } from "../../../api/generated/schema";
import { address } from "../PoCards";
import type { Po } from "../api";
import { BLANK, type Edited, type LineEdit } from "./patch";

type Address = components["schemas"]["OrgAddressRead"];
export type Errors = Record<string, string>;

/** A control in a table cell: a visually hidden label, the error beneath, the same context the form controls read. */
function Cell({ id, text, error, children }: { id: string; text: string; error?: string; children: ReactNode }) {
  return <FieldContext.Provider value={{ describedBy: error ? `${id}-error` : "", error, id, required: false }}>
    <label htmlFor={id} className="visually-hidden">{text}</label>
    {children}
    {error && <p className="field-error" id={`${id}-error`}>{error}</p>}
  </FieldContext.Provider>;
}

export const lineId = (n: number, key: "quantity" | "tax" | "freight") => `po-line-${n}-${key}`;

export function OrderCard({ po, edited, set, errors }: { po: Po; edited: Edited; set: (patch: Partial<Edited>) => void; errors: Errors }) {
  const options = useLoad(async () => {
    const { data, response } = await apiClient.GET("/api/v1/purchase-orders/{id}/ship-to-options", { params: { path: { id: po.id } } });
    if (data) return { kind: "ok", rows: data } as { kind: "ok"; rows: Address[] } | { kind: "forbidden" };
    if (response?.status === 403) return { kind: "forbidden" } as { kind: "ok"; rows: Address[] } | { kind: "forbidden" };
    throw new Error(failure(undefined, "Loading the Ship To choices"));
  }, [po.id]);
  const rows = options.data?.kind === "ok" ? options.data.rows : [];
  const known = rows.some(a => a.id === edited.ship_to_address_id);
  return <section className="req-card" aria-labelledby="po-order-title">
    <header className="req-card-head"><h2 id="po-order-title">Order</h2></header>
    <dl className="project-facts">
      <div><dt>Bill To</dt><dd>{address(po.bill_to)}</dd></div>
      <div><dt>Requisition</dt><dd><a href={`/requisitions/${po.requisition_id}`}>Open the requisition</a></dd></div>
    </dl>
    {options.data?.kind === "forbidden"
      ? <p role="status">Ship To choices are unavailable with your access.</p>
      : <Field id="po-ship-to" label="Ship To" error={errors["po-ship-to"]} help={edited.ship_to_address_id && !known && options.data ? "The chosen Ship To is no longer active. Pick another." : "Pick an active Ship To of this unit or above."}>
        <Select value={edited.ship_to_address_id ?? ""} onChange={e => set({ ship_to_address_id: e.target.value || null })}>
          {edited.ship_to_address_id === null && <option value="" disabled>Not set</option>}
          {edited.ship_to_address_id && !known && <option value={edited.ship_to_address_id} disabled>No longer active</option>}
          {rows.map(a => <option key={a.id} value={a.id}>{address(a)}{a.id === po.ship_to_address_id ? " (from the requisition)" : ""}</option>)}
        </Select>
      </Field>}
    <Field id="po-terms" label="Terms" error={errors["po-terms"]} help="Printed on the PDF. Up to 4000 characters.">
      <textarea id="po-terms" aria-describedby={errors["po-terms"] ? "po-terms-help po-terms-error" : "po-terms-help"} aria-invalid={errors["po-terms"] ? true : undefined} rows={3} maxLength={4000} value={edited.terms} onChange={e => set({ terms: e.target.value })} />
    </Field>
    <Field id="po-needed-by" label="Needed by" error={errors["po-needed-by"]} help="Leave empty when there is no date.">
      <Input type="date" autoComplete="off" inputMode="none" value={edited.needed_by} onChange={e => set({ needed_by: e.target.value })} />
    </Field>
  </section>;
}

export function VendorCard({ po }: { po: Po }) {
  const name = typeof po.vendor_snapshot.trading_name === "string" ? po.vendor_snapshot.trading_name : "Vendor";
  const now = useLoad(async () => {
    const { data, response } = await apiClient.GET("/api/v1/vendors/{id}/eligibility", { params: { path: { id: po.vendor_id }, query: { unit_id: po.unit_id } } });
    if (data) return data;
    if (response?.status === 403) return null;
    throw new Error("Eligibility could not be loaded.");
  }, [po.vendor_id, po.unit_id]);
  const snapshot = po.vendor_snapshot.eligibility as { eligible?: boolean } | undefined;
  return <section className="req-card" aria-labelledby="po-vendor-title">
    <header className="req-card-head"><h2 id="po-vendor-title">Vendor</h2></header>
    <dl className="project-facts">
      <div><dt>Vendor</dt><dd><a href={`/vendors/${po.vendor_id}`}>{name}</a></dd></div>
      <div><dt>Eligibility</dt><dd>{now.data ? <Eligibility eligibility={now.data} variant="field" />
        : now.loading && !now.error ? "Checking…" : snapshot?.eligible === false ? "Not eligible when drafted" : "Eligible when drafted"}</dd></div>
      <div><dt>Send To</dt><dd>{po.recipients.length === 0 ? "—" : po.recipients.map((r, i) => <span key={i} className="req-sub"><Person name={String(r.name)} email={String(r.email)} /></span>)}</dd></div>
    </dl>
    {snapshot?.eligible === false && <p className="req-sub">Ordered by override when this draft was created; the reason is in History.</p>}
    <p className="req-sub">The vendor's Service and Delivery contacts, set when the draft was created. The vendor and its recipients cannot change on a draft.</p>
  </section>;
}

export function LinesEditor({ po, edited, set, errors, projects }: { po: Po; edited: Edited; set: (patch: Partial<Edited>) => void; errors: Errors; projects: Map<string, string> }) {
  const pair = (v: string) => <Money zero="value" value={v} currency={po.currency} />;
  const doc = edited.freight_mode === "document";
  const setLine = (n: number, patch: Partial<LineEdit>) => set({ lines: { ...edited.lines, [n]: { ...(edited.lines[n] ?? BLANK), ...patch } } });
  const modeId = useId();
  return <section className="req-card req-table-card" aria-labelledby="po-lines-title">
    <header className="req-card-head"><h2 id="po-lines-title">Lines</h2><span className="req-count">{po.lines.length} {po.lines.length === 1 ? "line" : "lines"}</span></header>
    {errors["po-lines"] && <p className="field-error" id="po-lines">{errors["po-lines"]}</p>}
    <fieldset className="po-freight-mode"><legend>Freight</legend>
      <label><input type="radio" name={modeId} checked={!doc} onChange={() => set({ freight_mode: "line" })} />Per line</label>
      <label><input type="radio" name={modeId} checked={doc} onChange={() => set({ freight_mode: "document" })} />Per document</label>
      <p className="req-sub">Per document spreads one freight figure across the lines by their value.</p>
    </fieldset>
    {doc && <Field id="po-document-freight" label="Document freight" error={errors["po-document-freight"]} help="One figure for the whole order.">
      <MoneyInput currency={po.currency} autoComplete="off" value={edited.document_freight} onChange={e => set({ document_freight: e.target.value })} />
    </Field>}
    <table className="vendor-table po-lines">
      <caption className="visually-hidden">Purchase order lines</caption>
      <thead><tr><th scope="col">Line</th><th scope="col" data-numeric>Quantity</th><th scope="col" data-numeric>Unit price</th><th scope="col" data-numeric>Price</th><th scope="col" data-numeric>Tax</th><th scope="col" data-numeric>Freight</th><th scope="col" data-numeric>Line total</th></tr></thead>
      <tbody>{po.lines.map(l => {
        const e = edited.lines[l.line_no] ?? BLANK;
        const qid = lineId(l.line_no, "quantity"), tid = lineId(l.line_no, "tax"), fid = lineId(l.line_no, "freight");
        return <tr key={l.line_no}>
          <td data-label="Line"><strong>{l.line_no}. {l.description}</strong><span className="req-sub">{l.uom_code}</span>
            <span className="req-sub"><a href={`/projects/${l.project_id}`}>{projects.get(l.project_id) ?? "Project"}</a> · {l.department_code ?? "—"} · {l.ledger_account_code ?? "—"}</span></td>
          <td data-label="Quantity" data-numeric><Cell id={qid} text={`Quantity, line ${l.line_no}`} error={errors[qid]}><Input autoComplete="off" inputMode="decimal" value={e.quantity} onChange={ev => setLine(l.line_no, { quantity: ev.target.value })} /></Cell></td>
          <td data-label="Unit price" data-numeric>{pair(l.unit_price)}</td>
          <td data-label="Price" data-numeric>{pair(l.price)}</td>
          <td data-label="Tax" data-numeric><Cell id={tid} text={`Tax, line ${l.line_no}`} error={errors[tid]}><MoneyInput currency={po.currency} autoComplete="off" value={e.tax} onChange={ev => setLine(l.line_no, { tax: ev.target.value })} /></Cell></td>
          <td data-label="Freight" data-numeric>{doc ? pair(l.freight) : <Cell id={fid} text={`Freight, line ${l.line_no}`} error={errors[fid]}><MoneyInput currency={po.currency} autoComplete="off" value={e.freight} onChange={ev => setLine(l.line_no, { freight: ev.target.value })} /></Cell>}</td>
          <td data-label="Line total" data-numeric>{pair(l.line_total)}</td></tr>;
      })}</tbody>
    </table>
  </section>;
}

/** The server's totals, never a client sum. */
export function TotalsBar({ po, unsaved }: { po: Po; unsaved: boolean }) {
  const pair = (v: string) => <Money zero="value" value={v} currency={po.currency} />;
  return <section className="req-card" aria-label="Totals">
    <dl className="project-facts po-totals">
      <div><dt>Price</dt><dd>{pair(po.price_total)}</dd></div>
      <div><dt>Tax</dt><dd>{pair(po.tax_total)}</dd></div>
      <div><dt>Freight</dt><dd>{pair(po.freight_total)}</dd></div>
      <div><dt><strong>Total</strong></dt><dd><strong>{pair(po.total)}</strong></dd></div>
      {po.currency !== po.converted_currency && <div><dt>Total in {po.converted_currency}</dt><dd><Money zero="value" value={po.total_converted} currency={po.converted_currency} /></dd></div>}
    </dl>
    {unsaved && <p className="req-sub" role="status">Totals update when you save.</p>}
  </section>;
}

/** Sentences built from the server's strings; nothing here adds, subtracts or multiplies money. */
export function BudgetPreview({ po, refreshKey, onProjects }: { po: Po; refreshKey: number; onProjects: (names: Map<string, string>) => void }) {
  const load = useLoad(async () => {
    const { data, error, response } = await apiClient.GET("/api/v1/purchase-orders/{id}/budget-summary", { params: { path: { id: po.id } } });
    if (data) { onProjects(new Map(data.projects.map(p => [p.project_id, `${p.project_code} ${p.project_name}`]))); return { kind: "ok", projects: data.projects } as const; }
    if (response?.status === 403) return { kind: "forbidden" } as const;
    throw new Error(failure(error as Problem | undefined, "Loading the budget preview"));
  }, [po.id, refreshKey]);
  const state = load.data;
  return <Card title="Budget Preview" load={load}>
    {state?.kind === "forbidden" && <p role="status">Budget figures are unavailable: your access does not include this project's balance.</p>}
    {state?.kind === "ok" && state.projects.map(p => <p key={p.project_id}>Issuing will commit <Money zero="value" value={p.this_po} currency={p.currency} /> on {p.project_code} {p.project_name}. Available after issue: <Money zero="value" value={p.available_after_issue} currency={p.currency} />.</p>)}
    {po.tax_consumes_budget === false && <p className="req-sub">Tax is shown on this order but does not count against the budget.</p>}
    {po.freight_consumes_budget === false && <p className="req-sub">Freight is shown on this order but does not count against the budget.</p>}
  </Card>;
}

