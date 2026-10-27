import Money from "../../ui/Money";
import type { Comparison } from "../comparison/api";
import { fieldId, rowKey, standing, type Row } from "./api";

/** One block per RFQ line: a quantity per vendor that quoted (a line may split), a reason where the choice is not the plain lowest. */
export default function Lines({ c, rows, setRow, required, locked }: { c: Comparison; rows: Record<string, Row>; setRow: (key: string, patch: Partial<Row>) => void; required: boolean; locked: boolean }) {
  return <div id="aw-lines" tabIndex={-1} className="aw-lines">{c.lines.map(line => <fieldset key={line.rfq_line_id} className="req-card aw-line" disabled={locked}>
    <legend>Line {line.line_no} · {line.description} · Quantity {line.quantity}</legend>
    {line.cells.filter(cell => !cell.not_quoted).map(cell => {
      const key = rowKey(line.rfq_line_id, cell.vendor_id), row = rows[key] ?? { quantity: "", reason: "" }, kind = standing(line, cell.vendor_id);
      const name = c.vendors.find(v => v.vendor_id === cell.vendor_id)?.vendor_name ?? "Vendor", awarded = /[1-9]/.test(row.quantity);
      const id = fieldId(line.rfq_line_id, cell.vendor_id), need = kind === "tie" || (kind === "other" && required);
      return <div key={key} className="aw-row">
        <span className="aw-vendor">{name}{kind === "lowest" && <span className="req-sub">Lowest</span>}{kind === "tie" && <span className="req-sub">Tied for lowest</span>}</span>
        <span className="aw-landed"><span className="req-sub">Landed</span><Money value={cell.landed ?? null} currency={c.currency} zero="value" /></span>
        <label className="aw-qty"><span className="t-label">Quantity for {name}, line {line.line_no}</span>
          <input inputMode="decimal" className="numeric" value={row.quantity} onChange={e => setRow(key, { quantity: e.target.value })} /></label>
        {awarded && kind !== "lowest" && <label className="aw-reason"><span className="t-label">{kind === "tie" ? "Tie Override Reason" : "Justification"}{need && <span className="req-tag"><span className="rq-need">Required</span></span>}<span className="visually-hidden"> for {name}, line {line.line_no}</span></span>
          <input id={id} maxLength={500} required={need} value={row.reason} onChange={e => setRow(key, { reason: e.target.value })} /></label>}
      </div>;
    })}
  </fieldset>)}</div>;
}
