import * as Dialog from "@radix-ui/react-dialog";
import { X } from "@phosphor-icons/react";
import { restoreFocus } from "../../../lib/focus";
import Person from "../../ui/Person";
import Signal from "../../ui/Signal";
import { formatDateTime } from "../../requisitions/api";
import { sentSignal, type PoTransmission } from "../api";
import { templateWords } from "./recovery";
import "../../../styles/approvals.css";

/** What the server carries about one send; mounted only while open. Recovery lives in the header, not here. */
export default function TransmissionSheet({ row, opener, onClose }: { row: PoTransmission; opener: HTMLElement | null; onClose: () => void }) {
  const s = sentSignal(row);
  return <Dialog.Root open onOpenChange={open => { if (!open) onClose(); }}>
    <Dialog.Portal>
      <Dialog.Overlay className="appr-scrim" />
      <Dialog.Content className="appr-sheet" aria-describedby="tx-sheet-desc" onCloseAutoFocus={event => { event.preventDefault(); restoreFocus(opener); }}>
        <header className="appr-sheet-head">
          <div><Dialog.Title>Email to {row.recipient_email}</Dialog.Title>
            <Dialog.Description id="tx-sheet-desc" className="visually-hidden">The record of one email sent for this purchase order.</Dialog.Description></div>
          <Dialog.Close className="appr-close" aria-label="Close"><X aria-hidden="true" />Close</Dialog.Close>
        </header>
        <dl className="project-facts">
          <div><dt>Recipient</dt><dd><Person name={row.recipient_name} email={row.recipient_email} /></dd></div>
          <div><dt>Version</dt><dd>v{row.revision + 1}</dd></div>
          <div><dt>Template</dt><dd>{templateWords(row.template_version)} <span className="req-sub">{row.template_version}</span></dd></div>
          <div><dt>Attempt</dt><dd>{row.attempt}</dd></div>
          <div><dt>Queued</dt><dd>{formatDateTime(row.created_at)}</dd></div>
          <div><dt>Result</dt><dd><Signal tone={s.tone} label={s.label} why={row.result_reason && <p className="sig-why">{row.result_reason}</p>} /></dd></div>
          <div><dt>Result at</dt><dd>{row.result_at ? formatDateTime(row.result_at) : "Not reported yet"}</dd></div>
          <div><dt>Provider Message ID</dt><dd>{row.provider_message_id ? <span className="po-mono">{row.provider_message_id}</span> : "Not assigned yet"}</dd></div>
        </dl>
      </Dialog.Content>
    </Dialog.Portal>
  </Dialog.Root>;
}
