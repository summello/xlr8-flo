import { FilePdf } from "@phosphor-icons/react";
import Money from "../../ui/Money";
import { formatDate } from "../../requisitions/api";
import type { VendorCopyRead } from "../api";

// Decisions 170/171: category names only, never values. The categories are fixed by decision, so this is a constant.
const NEVER_SENT = ["Chartfields (project, department, ledger account)", "Budget figures", "Internal notes", "Uploaded files: not sent until scanning is available"];

type Address = NonNullable<VendorCopyRead["vendor_copy"]["ship_to"]>;
const address = (a: Address) => [a.line1, a.line2, [a.city, a.region, a.postal_code].filter(Boolean).join(" "), a.country].filter(Boolean).join("\n");

/** The supplier's email and PDF, drawn only from `VendorCopyRead` (PO-014, EML-005): the page holds nothing the server withheld. */
export default function VendorCopyView({ copy, poId }: { copy: VendorCopyRead; poId: string }) {
  const c = copy.vendor_copy;
  const money = (v: string) => <Money value={v} currency={c.currency} zero="value" />;
  return <div className="po-vendor-copy">
    <p>{copy.issued ? `Version v${c.revision + 1} as issued on ${c.issued_on ? formatDate(c.issued_on) : "—"}, template ${copy.template_version}` : "Not issued yet. This is what the supplier will receive when it is issued; the PDF is generated at issue."}</p>
    <section aria-labelledby="vc-email"><h3 id="vc-email">Email</h3>
      <div className="po-email">{copy.email_summary}</div>
      {copy.issued && <p>Attachment: PDF</p>}
    </section>
    <section aria-labelledby="vc-pdf"><h3 id="vc-pdf">PDF</h3>
      {copy.issued && <p><a className="req-link-button" href={`/api/v1/purchase-orders/${poId}/pdf`} target="_blank" rel="noreferrer"><FilePdf aria-hidden="true" weight="regular" />Open PDF</a></p>}
      <article className="pdf-page" aria-label="Vendor copy of the purchase order">
        {c.letterhead_address && <p className="po-pre">{c.letterhead_address}</p>}
        <h4>Purchase Order {c.number}</h4>
        <p>{c.revision > 0 && <>Revision {c.revision}<br /></>}Issued: {c.issued_on ? formatDate(c.issued_on) : "—"}{c.needed_by && <><br />Needed by: {formatDate(c.needed_by)}</>}<br />Currency: {c.currency}</p>
        <div><h5>Vendor</h5><p>{c.vendor_trading_name}<br />{c.vendor_legal_name}</p></div>
        <div><h5>Bill To</h5><p className="po-pre">{address(c.bill_to)}</p></div>
        {c.ship_to && <div><h5>Ship To</h5><p className="po-pre">{address(c.ship_to)}</p></div>}
        <div className="po-vc-scroll"><table className="vendor-table po-lines">
          <caption className="visually-hidden">Lines</caption>
          <thead><tr>{["#", "Description", "Qty", "Unit price", "Price", "Tax", "Freight", "Total"].map(h => <th key={h} scope="col">{h}</th>)}</tr></thead>
          <tbody>{c.lines.map(l => <tr key={l.line_no}>
            <td data-numeric>{l.line_no}</td><td>{l.description}</td><td data-numeric>{l.quantity} {l.uom_code}</td>
            <td data-numeric>{money(l.unit_price)}</td><td data-numeric>{money(l.price)}</td><td data-numeric>{money(l.tax)}</td><td data-numeric>{money(l.freight)}</td><td data-numeric>{money(l.line_total)}</td>
          </tr>)}</tbody>
        </table></div>
        <dl className="po-totals">
          <dt>Price</dt><dd>{money(c.price_total)}</dd><dt>Tax</dt><dd>{money(c.tax_total)}</dd>
          <dt>Freight</dt><dd>{money(c.freight_total)}</dd><dt>Total</dt><dd>{money(c.total)}</dd>
        </dl>
        {c.terms && <div><h5>Terms</h5><p className="po-pre">{c.terms}</p></div>}
        {(c.notes ?? []).length > 0 && <div><h5>Notes</h5>{c.notes!.map((n, i) => <p key={i}>{formatDate(n.created_on)}: {n.body}</p>)}</div>}
        {c.standard_terms && <div><h5>Standard terms</h5><p className="po-pre">{c.standard_terms}</p></div>}
      </article>
    </section>
    <section aria-labelledby="vc-never"><h3 id="vc-never">Never sent to the vendor</h3>
      <ul>{NEVER_SENT.map(n => <li key={n}>{n}</li>)}</ul>
    </section>
  </div>;
}
