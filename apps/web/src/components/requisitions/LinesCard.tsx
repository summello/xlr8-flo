import { Check, Plus, Trash } from "@phosphor-icons/react";
import Money from "./ReqMoney";
import { lineTotal, newLine, estimateOf, type DraftLine } from "./model";
import type { LookupOption } from "./api";

type Props = {
  lines: DraftLine[]; currency: string; units: LookupOption[]; errors: Record<string, string>;
  onChange: (lines: DraftLine[]) => void; addRef: React.RefObject<HTMLButtonElement | null>;
};

type CellControl = { id: string; required: true; "aria-invalid"?: true; "aria-describedby": string };
/** The forms-kit pattern for a cell: label with the Required/Done tag, `required` on the control, persistent helper text, field error. */
function CellField({ n, field, label, help, error, children }: { n: number; field: string; label: string; help: string; error?: string; children: (props: CellControl) => React.ReactNode }) {
  const id = `line-${n}-${field}`;
  return <div className="req-cell-field">
    <label className="t-label req-cell-label" htmlFor={id}>{label}, line {n}
      <span className="req-tag" aria-hidden="true"><span className="rq-need">Required</span><span className="rq-done"><Check weight="regular" />Done</span></span></label>
    {children({ id, required: true, ...(error ? { "aria-invalid": true as const } : {}), "aria-describedby": error ? `${id}-help ${id}-error` : `${id}-help` })}
    <span className="field-help" id={`${id}-help`}>{help}</span>
    {error && <span className="req-cell-error" id={`${id}-error`}>{error}</span>}
  </div>;
}

const COLUMNS = ["Type", "Description", "Quantity", "Unit", "Unit price", "Line total"] as const;

/** REQ-006/007: one row per line with an explicit type; stacked cards below 660px of card width. */
export default function LinesCard({ lines, currency, units, errors, onChange, addRef }: Props) {
  const patch = (key: string, change: Partial<DraftLine>) => onChange(lines.map(l => l.key === key ? { ...l, ...change } : l));
  const err = (n: number, field: string) => errors[`line-${n}-${field}`];
  return <section className="req-card req-lines" id="lines-card" tabIndex={-1} aria-labelledby="lines-title">
    <header className="req-card-head"><h2 id="lines-title">Lines</h2><span className="req-count" role="status">{lines.length} {lines.length === 1 ? "line" : "lines"}</span></header>
    <div className="req-toolbar">
      <button type="button" ref={addRef} id="add-item-line" onClick={() => onChange([...lines, newLine("item")])}><Plus aria-hidden="true" weight="regular" />Add item line</button>
      <button type="button" onClick={() => onChange([...lines, newLine("service")])}><Plus aria-hidden="true" weight="regular" />Add service line</button>
      <p className="field-help">Items and services are tagged separately.</p>
    </div>
    {lines.length === 0 ? <div className="req-empty"><h3>No Lines Yet</h3><p>Add an item or a service line, or pick one from the catalogue suggestions.</p></div> :
      <table className="req-lines-table">
        <thead><tr>{COLUMNS.map(c => <th key={c} scope="col" data-numeric={c === "Quantity" || c === "Unit price" || c === "Line total" || undefined}>{c}</th>)}<th scope="col"><span className="visually-hidden">Remove</span></th></tr></thead>
        <tbody>{lines.map((line, index) => {
          const n = index + 1;
          const catalogue = line.catalogue_item_id !== null;
          const total = lineTotal(line, currency);
          return <tr key={line.key} id={`line-${n}`} tabIndex={-1}>
            <td data-label="Type">{catalogue ? <span className="req-tag-type">{line.line_type === "item" ? "Item" : "Service"}</span> :
              <select id={`line-${n}-line_type`} aria-label={`Type, line ${n}`} value={line.line_type} onChange={e => patch(line.key, { line_type: e.target.value as DraftLine["line_type"] })}><option value="item">Item</option><option value="service">Service</option></select>}</td>
            <td data-label="Description">{catalogue ? <span>{line.description}</span> :
              <CellField n={n} field="description" label="Description" help="What is being bought, in words a buyer can act on." error={err(n, "description")}>
                {p => <input {...p} aria-label={`Description, line ${n}`} value={line.description} maxLength={500} onChange={e => patch(line.key, { description: e.target.value })} />}</CellField>}</td>
            <td data-label="Quantity" data-numeric><CellField n={n} field="quantity" label="Quantity" help="Greater than zero; decimals allowed." error={err(n, "quantity")}>
              {p => <input {...p} className="numeric" inputMode="decimal" autoComplete="off" aria-label={`Quantity, line ${n}`} value={line.quantity} onChange={e => patch(line.key, { quantity: e.target.value })} />}</CellField></td>
            <td data-label="Unit">{catalogue ? <span>{line.uom_code}</span> :
              <CellField n={n} field="uom_code" label="Unit" help="The unit of measure." error={err(n, "uom_code")}>
                {p => <select {...p} aria-label={`Unit, line ${n}`} value={line.uom_code} onChange={e => patch(line.key, { uom_code: e.target.value })}>
                  <option value="">Choose</option>{units.map(u => <option key={u.value} value={u.value}>{u.label}</option>)}
                  {line.uom_code && !units.some(u => u.value === line.uom_code) && <option value={line.uom_code}>{line.uom_code}</option>}</select>}</CellField>}</td>
            <td data-label="Unit price" data-numeric><CellField n={n} field="unit_price" label="Unit price" help={`Price for one unit, in ${currency}.`} error={err(n, "unit_price")}>
              {p => <input {...p} className="numeric" inputMode="decimal" autoComplete="off" aria-label={`Unit price, line ${n}`} value={line.unit_price} onChange={e => patch(line.key, { unit_price: e.target.value })} />}</CellField></td>
            <td data-label="Line total" data-numeric>{total === null ? <span aria-label={`Line total, line ${n}: not yet`}>—</span> : <Money value={total} currency={currency} />}</td>
            <td><button type="button" className="req-icon-button" aria-label={`Remove line ${n}`} onClick={() => { onChange(lines.filter(l => l.key !== line.key)); requestAnimationFrame(() => addRef.current?.focus()); }}><Trash aria-hidden="true" weight="regular" /></button></td>
          </tr>;
        })}</tbody>
      </table>}
    <p className="req-total"><span>Estimated total</span><Money value={estimateOf(lines, currency)} currency={currency} /></p>
  </section>;
}
