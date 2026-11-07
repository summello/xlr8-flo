import Field from "../../form/Field";
import Input from "../../form/Input";
import type { CardProps } from "./cards";
import Textarea from "./Textarea";

export default function ItemsCard({ rfq, mode, form, setForm, errors, linesNote }: CardProps) {
  return <section className="req-card req-wide" aria-labelledby="rfq-card-items">
    <header className="req-card-head"><h2 id="rfq-card-items">Items and Specifications</h2></header>
    <Field id="rfq-specifications" label="Specifications" required help="What the vendor must meet, in your words." error={errors["rfq-specifications"]}>
      <Textarea rows={5} value={form.specifications} onChange={e => setForm(f => ({ ...f, specifications: e.target.value }))} />
    </Field>
    <h3 id="rfq-lines" tabIndex={-1}>Lines</h3>
    {linesNote && <p role="alert" className="field-error">{linesNote} Your quantities are kept.</p>}
    <table className="vendor-table">
      <caption className="visually-hidden">Lines on this RFQ{mode === "amend" ? "; quantities and specifications can change" : ""}</caption>
      <thead><tr><th scope="col">Line</th><th scope="col">Description</th><th scope="col">Quantity</th><th scope="col">Line Specification</th></tr></thead>
      <tbody>{rfq.lines.map(l => <tr key={l.id}>
        <td data-label="Line">{l.line_no}</td>
        <td data-label="Description">{l.description}<span className="req-sub">{l.requisition_number}</span></td>
        <td data-label="Quantity">{mode === "amend"
          ? <Field id={`rfq-qty-${l.requisition_line_id}`} label={`Quantity, line ${l.line_no} (${l.uom_code})`} help="It can rise; it can only fall on a requisition that is not fully sourced."><Input autoComplete="off" inputMode="decimal" value={form.qty[l.requisition_line_id] ?? ""} onChange={e => setForm(f => ({ ...f, qty: { ...f.qty, [l.requisition_line_id]: e.target.value } }))} /></Field>
          : <>{l.quantity} {l.uom_code}</>}</td>
        <td data-label="Line Specification"><Field id={`rfq-spec-${l.id}`} label={`Specification, line ${l.line_no}`} help="Optional."><Textarea value={form.lineSpecs[l.id] ?? ""} onChange={e => setForm(f => ({ ...f, lineSpecs: { ...f.lineSpecs, [l.id]: e.target.value } }))} /></Field></td>
      </tr>)}</tbody>
    </table>
  </section>;
}
