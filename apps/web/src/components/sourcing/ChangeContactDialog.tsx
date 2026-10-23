import * as Dialog from "@radix-ui/react-dialog";
import { UserSwitch, X } from "@phosphor-icons/react";
import { useRef, useState } from "react";
import { apiClient } from "../../api/client";
import { failure, mutationHeaders, type Problem } from "../requisitions/api";
import { useLoad } from "../requisitions/useLoad";
import type { Invitee, Rfq } from "./api";

/** A bounced invitation: pick the vendor's contacts again (PATCH with the RFQ's revision), then resend (POST with a key). */
export default function ChangeContactDialog({ rfq, invitee, onDone }: { rfq: Rfq; invitee: Invitee; onDone: () => void }) {
  const [open, setOpen] = useState(false);
  const [chosen, setChosen] = useState<string[]>(invitee.contact_ids);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const key = useRef<string | null>(null);
  const keep = useRef<HTMLButtonElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const name = String(invitee.vendor_snapshot.trading_name ?? "the vendor");
  const contacts = useLoad(async () => {
    if (!open) return null;
    const { data, error } = await apiClient.GET("/api/v1/vendors/{id}/contacts", { params: { path: { id: invitee.vendor_id } } });
    if (!data) throw new Error(failure(error, "Loading contacts"));
    return data;
  }, [open, invitee.vendor_id]);

  const save = async () => {
    if (chosen.length === 0) { setMessage("Choose at least one contact to send the RFQ to."); return; }
    key.current ??= crypto.randomUUID();
    setBusy(true); setMessage(null);
    try {
      const path = { id: rfq.id, vendor_id: invitee.vendor_id };
      const patched = await apiClient.PATCH("/api/v1/sourcing/rfqs/{id}/invitees/{vendor_id}", { params: { path }, headers: { ...mutationHeaders(key.current), "If-Match": String(rfq.edit_revision) }, body: { contact_ids: chosen } });
      if (!patched.data) { setMessage(failure(patched.error as Problem | undefined, "Changing the contact")); return; }
      const sent = await apiClient.POST("/api/v1/sourcing/rfqs/{id}/invitees/{vendor_id}/resend", { params: { path }, headers: mutationHeaders(key.current) });
      if (!sent.data) { setMessage(failure(sent.error as Problem | undefined, "Resending the invitation")); return; }
      setOpen(false); key.current = null; onDone();
    } catch {
      setMessage(failure(undefined, "Changing the contact"));
    } finally { setBusy(false); }
  };

  return <Dialog.Root open={open} onOpenChange={next => { if (busy && !next) return; setOpen(next); if (next) { setChosen(invitee.contact_ids); setMessage(null); key.current = null; } }}>
    <button type="button" ref={trigger} onClick={() => setOpen(true)} aria-label={`Change Contact for ${name}`}><UserSwitch aria-hidden="true" weight="regular" />Change Contact</button>
    <Dialog.Portal><Dialog.Overlay className="project-dialog-overlay" />
      <Dialog.Content className="project-dialog material-surface material-shadow req-dialog" data-shadow="lg"
        onOpenAutoFocus={e => { e.preventDefault(); keep.current?.focus(); }}
        onCloseAutoFocus={e => { e.preventDefault(); trigger.current?.focus(); }}
        onEscapeKeyDown={e => { if (busy) e.preventDefault(); }} onInteractOutside={e => { if (busy) e.preventDefault(); }}>
        <Dialog.Title>Change Contact</Dialog.Title>
        <Dialog.Description>The email to {name} bounced. Choose who should receive the RFQ; it is sent again to them.</Dialog.Description>
        {contacts.error ? <div role="alert"><p>{contacts.error}</p><button type="button" onClick={contacts.reload}>Try again</button></div>
          : !contacts.data ? <p className="req-skeleton req-skeleton-short" role="status" aria-label="Loading contacts" />
          : <fieldset className="perf-field"><legend>Send To</legend>{contacts.data.map(c => <label key={c.id}><input type="checkbox" checked={chosen.includes(c.id)} onChange={e => setChosen(list => e.target.checked ? [...list, c.id] : list.filter(id => id !== c.id))} /> {c.name} <span className="req-sub">{c.email}</span></label>)}</fieldset>}
        {message && <p role="alert" className="field-error">{message} Your choice is kept.</p>}
        <div className="req-actions">
          <button type="button" ref={keep} onClick={() => setOpen(false)} disabled={busy}>Keep Current Contact</button>
          <button type="button" onClick={() => void save()} aria-busy={busy || undefined} disabled={busy}>{busy ? "Sending…" : "Save and Resend"}</button>
        </div>
        <Dialog.Close aria-label="Close dialog" className="grid-sheet-close" disabled={busy}><X aria-hidden="true" weight="regular" /></Dialog.Close>
      </Dialog.Content>
    </Dialog.Portal>
  </Dialog.Root>;
}
