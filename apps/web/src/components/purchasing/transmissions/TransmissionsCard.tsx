import { useState } from "react";
import Person from "../../ui/Person";
import Signal from "../../ui/Signal";
import Card from "../../sourcing/Card";
import { formatDateTime } from "../../requisitions/api";
import type { Loaded } from "../../requisitions/useLoad";
import { sentSignal, type PoTransmission } from "../api";
import { CANCEL_EMAIL, currentSend, ISSUE_EMAIL, templateWords } from "./recovery";
import TransmissionSheet from "./TransmissionSheet";

const NOTICE: Partial<Record<PoTransmission["result"], (email: string) => string>> = {
  bounced: e => `${e} does not accept mail. It is on the suppression list and is never retried silently.`,
  complained: e => `${e} reported this email as spam. It is on the suppression list and is never retried silently.`,
  address_suppressed: e => `${e} is on the suppression list, so this email was not sent to it. It is never retried silently.`,
};

/** The sends of this purchase order: who has it now, then every email. The list belongs to the page, so a resend updates the header and this card together. */
export default function TransmissionsCard({ id, status, load }: { id: string; status: string; load: Loaded<PoTransmission[]> }) {
  const rows = load.data ?? [];
  const summary = currentSend(rows, id, status === "cancelled" ? CANCEL_EMAIL : ISSUE_EMAIL);
  const notices = summary.filter(r => NOTICE[r.result]);
  const [open, setOpen] = useState<{ row: PoTransmission; opener: HTMLElement } | null>(null);
  return <Card title="Transmissions" load={load}>
    {rows.length === 0 ? <p>Nothing has been sent to the vendor yet.</p> : <>
      <div><h3>Sent to Vendor</h3><ul className="vendor-list-plain">{summary.map(r => <li key={r.id}><Signal tone={sentSignal(r).tone} label={sentSignal(r).label} /><span className="req-sub"><Person name={r.recipient_name} email={r.recipient_email} /></span></li>)}</ul>
        {notices.map(r => <p key={r.id}>{NOTICE[r.result]!(r.recipient_email)}</p>)}
        {notices.length > 0 && <p className="req-sub">An organization administrator can remove an address under Administration, Sending Domain.</p>}</div>
      <h3>Send log</h3>
      <table className="vendor-table po-sends">
        <caption className="visually-hidden">Every email sent for this purchase order</caption>
        <thead><tr><th scope="col">Time</th><th scope="col">Recipient</th><th scope="col">Version</th><th scope="col">Template</th><th scope="col">Result</th><th scope="col"><span className="visually-hidden">Details</span></th></tr></thead>
        <tbody>{rows.map(r => <tr key={r.id}>
          <td data-label="Time">{formatDateTime(r.created_at)}</td><td data-label="Recipient"><Person name={r.recipient_name} email={r.recipient_email} /></td><td data-label="Version">v{r.revision + 1}</td><td data-label="Template">{templateWords(r.template_version)}</td>
          <td data-label="Result"><Signal tone={sentSignal(r).tone} label={sentSignal(r).label} why={r.result_reason && <p className="sig-why">{r.result_reason}</p>} /></td>
          <td data-label="Details"><button type="button" className="req-link-button" aria-label={`Details for ${r.recipient_email}, attempt ${r.attempt}`} onClick={e => setOpen({ row: r, opener: e.currentTarget })}>Details</button></td></tr>)}</tbody>
      </table></>}
    <button type="button" className="req-link-button" onClick={load.reload}>Refresh</button>
    {open && <TransmissionSheet row={open.row} opener={open.opener} onClose={() => setOpen(null)} />}
  </Card>;
}
