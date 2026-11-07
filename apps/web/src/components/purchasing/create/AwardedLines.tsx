import { WarningCircle } from "@phosphor-icons/react";
import type { TextareaHTMLAttributes } from "react";
import Field from "../../form/Field";
import { useFieldContext } from "../../form/context";
import Money from "../../ui/Money";
import Signal from "../../ui/Signal";
import { formatQuantity, scaled } from "../../../lib/decimal";
import type { AwardedLine, AwardedVendor } from "../api";

function Reason(props: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  const f = useFieldContext();
  return <textarea {...props} id={f.id} rows={3} maxLength={500} aria-required="true" aria-describedby={f.describedBy} aria-invalid={f.error === undefined ? undefined : true} />;
}

type Props = {
  vendor: AwardedVendor;
  lines: readonly AwardedLine[];
  canOverride: boolean;
  values: Readonly<Record<string, string>>;
  errors: Readonly<Record<string, string>>;
  groupErrors: Readonly<Record<string, string>>;
  vendorError?: string;
  reason: string;
  reasonError?: string;
  onQuantity: (rfqLineId: string, text: string) => void;
  onReason: (text: string) => void;
};

/** One vendor's open award lines, a tbody per requisition, one quantity field per line; no drag (decision 152). */
export default function AwardedLines({ vendor, lines, canOverride, values, errors, groupErrors, vendorError, reason, reasonError, onQuantity, onReason }: Props) {
  const groups = new Map<string, AwardedLine[]>();
  for (const l of [...lines].sort((a, b) => a.requisition_number.localeCompare(b.requisition_number) || a.line_no - b.line_no)) groups.set(l.requisition_id, [...(groups.get(l.requisition_id) ?? []), l]);
  const heading = `po-vendor-${vendor.vendor_id}`;
  return <section aria-labelledby={heading} className="po-create-vendor">
    <h3 id={`vendor-${vendor.vendor_id}`} tabIndex={-1}><span id={heading}>{vendor.vendor_name}</span></h3>
    {!vendor.eligible && <Signal tone="warning" label={vendor.reasons.length ? `Not eligible: ${vendor.reasons.join("; ")}` : "Not eligible"} />}
    {vendorError && <p className="field-error" role="alert"><WarningCircle aria-hidden="true" weight="regular" /><span>{vendorError}</span></p>}
    {!vendor.eligible && (canOverride
      ? <Field id={`override-${vendor.vendor_id}`} label="Override Reason" required error={reasonError} help="Kept with the purchase order. You can order from this vendor because you approve RFQs.">
        <Reason value={reason} onChange={e => onReason(e.target.value)} />
      </Field>
      : <p className="req-sub">Someone who can approve RFQs must order from this vendor, or leave the quantities blank.</p>)}
    <table className="req-lines-table rfq-picker po-create-table" aria-label={`Open lines for ${vendor.vendor_name}`}>
      <thead><tr><th scope="col">Line</th><th scope="col" data-numeric>Unit Price</th><th scope="col" data-numeric>Awarded</th><th scope="col" data-numeric>Already Ordered</th><th scope="col" data-numeric>Open</th><th scope="col" data-numeric>Quantity to Order</th></tr></thead>
      {[...groups].map(([requisitionId, rows]) => {
        const number = rows[0]!.requisition_number, gid = `${vendor.vendor_id}:${requisitionId}`;
        return <tbody key={requisitionId}>
          <tr><th scope="rowgroup" colSpan={6}>{number}</th></tr>
          {groupErrors[gid] && <tr><td colSpan={6}><p className="field-error" id={`group-${gid}`} tabIndex={-1}><WarningCircle aria-hidden="true" weight="regular" /><span>{groupErrors[gid]}</span></p></td></tr>}
          {rows.map(l => {
            const none = scaled(l.open_qty) === 0n, id = `qty-${l.rfq_line_id}`;
            return <tr key={l.rfq_line_id}>
              <td data-label="Line"><strong>{l.line_no}. {l.description}</strong><span className="req-sub">{l.uom_code}</span></td>
              <td data-label="Unit Price" data-numeric><Money value={l.unit_price} currency={l.currency} zero="value" /></td>
              <td data-label="Awarded" data-numeric className="numeric">{formatQuantity(l.awarded_qty)}</td>
              <td data-label="Already Ordered" data-numeric className="numeric">{formatQuantity(l.ordered_qty)}</td>
              <td data-label="Open" data-numeric className="numeric">{formatQuantity(l.open_qty)}</td>
              <td data-label="Quantity to Order" data-numeric>{none ? "Fully ordered" : <>
                <input id={id} className="numeric" inputMode="decimal" autoComplete="off" aria-label={`Quantity to order, line ${l.line_no} of ${number}`} aria-invalid={errors[l.rfq_line_id] ? true : undefined} aria-describedby={errors[l.rfq_line_id] ? `${id}-error` : undefined} value={values[l.rfq_line_id] ?? ""} onChange={e => onQuantity(l.rfq_line_id, e.target.value)} />
                {errors[l.rfq_line_id] && <p className="field-error" id={`${id}-error`}><WarningCircle aria-hidden="true" weight="regular" /><span>{errors[l.rfq_line_id]}</span></p>}</>}</td>
            </tr>;
          })}
        </tbody>;
      })}
    </table>
  </section>;
}
