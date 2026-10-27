import * as Dialog from "@radix-ui/react-dialog";
import { X } from "@phosphor-icons/react";
import { useEffect, useState } from "react";
import { apiClient } from "../../../api/client";
import Money from "../../ui/Money";
import Person from "../../ui/Person";
import { restoreFocus } from "../../../lib/focus";
import { formatDate, formatDateTime } from "../../requisitions/api";
import type { Rfq } from "../api";
import type { Bid } from "../bids/api";
import { Pills } from "./VerdictStrip";
import type { Comparison, Vendor } from "./api";

/** Layer 3: one vendor's bid. What the comparison carries is always shown; the keyed terms, answers and exceptions come from the bid read, which the server refuses to some users, and then they are omitted (not shown empty). */
export default function VendorSheet({ c, rfq, v, opener, onClose }: { c: Comparison; rfq: Rfq; v: Vendor; opener: HTMLElement | null; onClose: () => void }) {
  const [bid, setBid] = useState<Bid | null>(null);
  useEffect(() => {
    let live = true;
    apiClient.GET("/api/v1/sourcing/rfqs/{id}/bids/{bid_id}", { params: { path: { id: rfq.id, bid_id: v.bid_id } } })
      .then(({ data }) => { if (live && data && "lines" in data) setBid(data); }, () => undefined);
    return () => { live = false; };
  }, [rfq.id, v.bid_id]);
  const money = (value: string | null) => value === null ? "—" : <Money value={value} currency={c.currency} zero="value" />;
  const converted = v.currency !== c.currency;
  return <Dialog.Root open onOpenChange={next => { if (!next) onClose(); }}>
    <Dialog.Portal><Dialog.Overlay className="appr-scrim" />
      <Dialog.Content className="appr-sheet cmp-sheet" aria-describedby="cmp-sheet-desc" onCloseAutoFocus={e => { e.preventDefault(); restoreFocus(opener); }}>
        <header className="appr-sheet-head">
          <div><Dialog.Title>{v.vendor_name}</Dialog.Title>
            <Dialog.Description id="cmp-sheet-desc" className="req-sub">Submitted {formatDateTime(v.sent_at)} · amounts in {c.currency} unless marked</Dialog.Description>
            <span className="cmp-pills"><Pills v={v} /></span></div>
          <Dialog.Close className="appr-close" aria-label="Close"><X aria-hidden="true" />Close</Dialog.Close>
        </header>
        <div className="cmp-sheet-body" tabIndex={0} role="region" aria-label="Bid details">
          {v.late && <p className="cmp-note">Accepted late by override: {v.late.accepted_by_email ? <Person name={v.late.accepted_by_name} email={v.late.accepted_by_email} /> : v.late.accepted_by_name ?? "unknown"}, {formatDateTime(v.late.at)}. Reason: {v.late.reason}</p>}
          {converted && <p className="cmp-note">Converted from {v.currency} at {v.conversion.rate} ({v.conversion.source}, effective {formatDate(v.conversion.effective_date)}). The vendor's bid is not altered.</p>}
          <dl className="cmp-facts">
            <dt>Landed total</dt><dd>{money(v.landed_total)}</dd>
            <dt>Price</dt><dd>{money(v.price)}</dd>
            <dt>Tax and duty</dt><dd>{money(v.tax)}</dd>
            <dt>Freight</dt><dd>{money(v.freight)}</dd>
            <dt>Lead time</dt><dd>{v.lead_time_days === null ? "—" : `${v.lead_time_days} days`}</dd>
            <dt>Score</dt><dd>{v.score ?? "Not available"}</dd>
            {bid && <><dt>Valid until</dt><dd>{formatDate(bid.valid_until)}</dd>
              <dt>Delivery terms</dt><dd>{bid.delivery_terms ?? "—"}</dd>
              <dt>Warranty</dt><dd>{bid.warranty ?? "—"}</dd></>}
          </dl>
          <section aria-labelledby="cmp-sheet-lines"><h3 id="cmp-sheet-lines">Lines</h3>
            <div className="cmp-scroll"><table className="cmp-table"><caption className="visually-hidden">Lines quoted by {v.vendor_name}</caption>
              <thead><tr><th scope="col">Line</th><th scope="col">Quantity</th><th scope="col">Unit price ({v.currency})</th><th scope="col">Landed line</th></tr></thead>
              <tbody>{c.lines.map(l => { const cell = l.cells.find(x => x.vendor_id === v.vendor_id);
                return <tr key={l.rfq_line_id}><th scope="row">{l.line_no}. {l.description}</th>{!cell || cell.not_quoted ? <td colSpan={3}>Not quoted</td> : <><td className="numeric">{cell.quantity}</td><td className="numeric">{cell.unit_price}</td><td>{money(cell.landed)}</td></>}</tr>; })}</tbody></table></div></section>
          {bid && <section aria-labelledby="cmp-sheet-req"><h3 id="cmp-sheet-req">Requirements</h3>
            <ul className="vendor-list-plain">{bid.requirements.map(r => <li key={r.requirement_id}>{(rfq.requirements ?? []).find(q => q.id === r.requirement_id)?.text ?? "Requirement"}: <strong>{r.met ? "Met" : "Not Met"}</strong>{r.note && `, ${r.note}`}</li>)}</ul></section>}
          {bid && <section aria-labelledby="cmp-sheet-exc"><h3 id="cmp-sheet-exc">Exceptions</h3>
            <p>{bid.exceptions ?? "None"}</p></section>}
        </div>
      </Dialog.Content>
    </Dialog.Portal>
  </Dialog.Root>;
}
