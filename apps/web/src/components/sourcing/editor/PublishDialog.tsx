import * as Dialog from "@radix-ui/react-dialog";
import { PaperPlaneTilt, X } from "@phosphor-icons/react";
import { useRef } from "react";
import Person from "../../ui/Person";
import type { Rfq } from "../api";

/** Publishing is a promise to every invitee: say who receives what, and which files stay internal. */
export default function PublishDialog({ rfq, open, busy, message, onOpenChange, onConfirm }: { rfq: Rfq; open: boolean; busy: boolean; message: string | null; onOpenChange: (open: boolean) => void; onConfirm: () => void }) {
  const keep = useRef<HTMLButtonElement>(null);
  const files = rfq.attachments ?? [];
  return <Dialog.Root open={open} onOpenChange={next => { if (!(busy && !next)) onOpenChange(next); }}>
    <Dialog.Portal><Dialog.Overlay className="project-dialog-overlay" />
      <Dialog.Content className="project-dialog material-surface material-shadow req-dialog" data-shadow="lg" onOpenAutoFocus={e => { e.preventDefault(); keep.current?.focus(); }}
        onEscapeKeyDown={e => { if (busy) e.preventDefault(); }} onInteractOutside={e => { if (busy) e.preventDefault(); }}>
        <Dialog.Title>Publish RFQ</Dialog.Title>
        <Dialog.Description>{rfq.number} becomes version 1 and cannot be edited in place afterwards; later changes are amendments that tell every invitee. Each invitee below is emailed now.</Dialog.Description>
        <h3>Who Receives It</h3>
        {(rfq.invitees ?? []).length === 0 ? <p>No vendors are invited.</p> : <ul className="vendor-list-plain">{(rfq.invitees ?? []).map(i => <li key={i.vendor_id}><strong>{String(i.vendor_snapshot.trading_name ?? "Vendor")}</strong>
          <span className="req-sub">{(i.contacts ?? []).filter(c => c.chosen).length === 0 ? "No contact chosen" : (i.contacts ?? []).filter(c => c.chosen).map((c, n) => <span key={c.id}>{n > 0 && ", "}<Person name={c.name} email={c.email} /></span>)}</span></li>)}</ul>}
        <h3>Files</h3>
        {files.length === 0 ? <p>No files are attached.</p> : <>
          <p>Shared with vendors: {files.filter(f => f.vendor_visible).map(f => f.display_name).join(", ") || "none"}.</p>
          <p>Stay internal: {files.filter(f => !f.vendor_visible).map(f => f.display_name).join(", ") || "none"}.</p></>}
        {message && <p role="alert" className="field-error">{message}</p>}
        <div className="req-actions">
          <button type="button" ref={keep} onClick={() => onOpenChange(false)} disabled={busy}>Keep Editing</button>
          <button type="button" onClick={onConfirm} aria-busy={busy || undefined} disabled={busy}><PaperPlaneTilt aria-hidden="true" weight="regular" />{busy ? "Publishing…" : "Publish RFQ"}</button>
        </div>
        <Dialog.Close aria-label="Close dialog" className="grid-sheet-close" disabled={busy}><X aria-hidden="true" weight="regular" /></Dialog.Close>
      </Dialog.Content>
    </Dialog.Portal>
  </Dialog.Root>;
}
