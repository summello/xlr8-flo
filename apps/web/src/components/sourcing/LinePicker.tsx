import { WarningCircle } from "@phosphor-icons/react";
import type { components } from "../../api/generated/schema";
import { formatQuantity } from "../../lib/decimal";

export type SourceableLine = components["schemas"]["SourceableLine"];

const REASONS: Record<NonNullable<SourceableLine["reason"]>, string> = {
  another_unit: "Another business unit",
  different_currency: "Different currency",
  amendment_pending: "Amendment pending",
  on_rfq: "Already on an RFQ",
};

/** Why a row cannot be picked; `currency` is the currency of the first line chosen, if any. */
function disabledReason(line: SourceableLine, currency: string | null): string | null {
  if (!line.selectable) return line.reason ? REASONS[line.reason] : "Not available";
  return currency && line.currency !== currency ? REASONS.different_currency : null;
}

type Props = {
  rows: readonly SourceableLine[];
  /** Typed quantity text per checked line id. */
  selected: Readonly<Record<string, string>>;
  errors: Readonly<Record<string, string>>;
  currency: string | null;
  onToggle: (line: SourceableLine, checked: boolean) => void;
  onQuantity: (lineId: string, text: string) => void;
};

/** Checkbox rows grouped by requisition; no drag (decision 152). */
export default function LinePicker({ rows, selected, errors, currency, onToggle, onQuantity }: Props) {
  const groups = new Map<string, SourceableLine[]>();
  for (const r of rows) groups.set(r.requisition_number, [...(groups.get(r.requisition_number) ?? []), r]);
  return <table className="req-lines-table rfq-picker" aria-label="Approved requisition lines">
    <thead><tr><th scope="col">Line</th><th scope="col" data-numeric>Requested</th><th scope="col" data-numeric>Already Sourced</th><th scope="col" data-numeric>Open</th><th scope="col" data-numeric>Quantity to Source</th></tr></thead>
    {[...groups].map(([number, lines]) => <tbody key={number}>
      <tr><th scope="rowgroup" colSpan={5}>{number}</th></tr>
      {lines.map(l => {
        const why = disabledReason(l, currency);
        const checked = l.line_id in selected;
        const name = `line ${l.line_no} of ${number}`;
        return <tr key={l.line_id}>
          <td data-label="Line"><label className="rfq-pick"><input type="checkbox" checked={checked} disabled={why !== null} aria-label={`Source ${name}`} onChange={e => onToggle(l, e.target.checked)} />
            <span><strong>{l.line_no}. {l.description}</strong><span className="req-sub">{l.uom_code} · {l.currency}{why && ` · ${why}`}</span></span></label></td>
          <td data-label="Requested" data-numeric className="numeric">{formatQuantity(l.requested_qty)}</td>
          <td data-label="Already Sourced" data-numeric className="numeric">{formatQuantity(l.sourced_qty)}</td>
          <td data-label="Open" data-numeric className="numeric">{formatQuantity(l.open_qty)}</td>
          <td data-label="Quantity to Source" data-numeric>{checked && <>
            <input id={`qty-${l.line_id}`} className="numeric" inputMode="decimal" autoComplete="off" aria-label={`Quantity to source, ${name}`} aria-invalid={errors[l.line_id] ? true : undefined} aria-describedby={errors[l.line_id] ? `qty-${l.line_id}-error` : undefined} value={selected[l.line_id]} onChange={e => onQuantity(l.line_id, e.target.value)} />
            {errors[l.line_id] && <p className="field-error" id={`qty-${l.line_id}-error`}><WarningCircle aria-hidden="true" weight="regular" /><span>{errors[l.line_id]}</span></p>}</>}</td>
        </tr>;
      })}
    </tbody>)}
  </table>;
}
