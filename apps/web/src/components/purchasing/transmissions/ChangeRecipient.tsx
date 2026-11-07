import * as Dialog from "@radix-ui/react-dialog";
import { X } from "@phosphor-icons/react";
import { forwardRef, useId, useRef, useState } from "react";
import { apiClient } from "../../../api/client";
import { failure, mutationHeaders, type Problem } from "../../requisitions/api";
import { useLoad } from "../../requisitions/useLoad";
import type { PoTransmission } from "../api";
import "../../../styles/vendors.css";
import "../../../styles/projects.css";

/** Choose the vendor contacts the next send goes to; shared with E13-S16. Only mounted for a viewer who holds vendor.view. */
const ChangeRecipient = forwardRef<HTMLButtonElement, { poId: string; vendorId: string; onSent: (rows: PoTransmission[]) => void }>(function ChangeRecipient({ poId, vendorId, onSent }, opener) {
  const [open, setOpen] = useState(false), [opened, setOpened] = useState(0);
  const [chosen, setChosen] = useState<string[]>([]);
  const [busy, setBusy] = useState(false), [fieldError, setFieldError] = useState<string | null>(null), [refusal, setRefusal] = useState<string | null>(null);
  const key = useRef<string | null>(null), inFlight = useRef(false), first = useRef<HTMLInputElement>(null);
  const uid = useId();
  const contacts = useLoad(async () => {
    if (!open) return [];
    const { data, error } = await apiClient.GET("/api/v1/vendors/{id}/contacts", { params: { path: { id: vendorId } } });
    if (!data) throw new Error(failure(error as Problem | undefined, "Loading the vendor's contacts"));
    return data;
  }, [vendorId, open, opened]);

  const submit = async () => {
    if (inFlight.current) return;
    if (chosen.length < 1 || chosen.length > 20) { setFieldError("Choose between 1 and 20 contacts."); return; }
    inFlight.current = true; setBusy(true); setFieldError(null); setRefusal(null);
    key.current ??= crypto.randomUUID();
    try {
      const { data, error, response } = await apiClient.POST("/api/v1/purchase-orders/{id}/transmissions/resend", { params: { path: { id: poId } }, headers: mutationHeaders(key.current), body: { contact_ids: chosen } });
      if (data) { key.current = null; setOpen(false); onSent(data); return; }
      const problem = error as Problem | undefined;
      if (response.status === 422 || response.status === 409 || response.status === 403) key.current = null;
      (response.status === 422 ? setFieldError : setRefusal)(failure(problem, "Resending"));
    } catch { setRefusal(failure(undefined, "Resending")); }
    finally { inFlight.current = false; setBusy(false); }
  };

  return <Dialog.Root open={open} onOpenChange={next => { if (busy && !next) return; setOpen(next); }}>
    <button type="button" ref={opener} onClick={() => { setChosen([]); setFieldError(null); setRefusal(null); key.current = null; setOpened(n => n + 1); setOpen(true); }}>Change Recipient</button>
    <Dialog.Portal><Dialog.Overlay className="project-dialog-overlay" />
      <Dialog.Content className="project-dialog material-surface material-shadow req-dialog vendor-dialog" data-shadow="lg"
        onOpenAutoFocus={e => { e.preventDefault(); first.current?.focus(); }}
        onCloseAutoFocus={e => { e.preventDefault(); (opener as React.RefObject<HTMLButtonElement | null> | null)?.current?.focus(); }}>
        <Dialog.Title>Change Recipient</Dialog.Title>
        <Dialog.Description>The next send goes only to the contacts you choose.</Dialog.Description>
        {contacts.error ? <p role="alert">{contacts.error} <button type="button" onClick={contacts.reload}>Try again</button></p>
          : !contacts.data ? <p className="req-skeleton" role="status" aria-label="Loading contacts" />
          : <fieldset className="vendor-dialog-units" aria-describedby={fieldError ? `${uid}-error` : undefined}><legend className="t-label">Contacts</legend>
            {contacts.data.length === 0 && <p>This vendor has no contacts.</p>}
            {contacts.data.map((c, i) => <label key={c.id} className="project-checkbox"><input type="checkbox" ref={i === 0 ? first : undefined} checked={chosen.includes(c.id)}
              onChange={e => setChosen(s => e.target.checked ? [...s, c.id] : s.filter(x => x !== c.id))} />{c.name} <span className="req-sub">{c.email} · {c.type}</span></label>)}
            {fieldError && <p role="alert" id={`${uid}-error`} className="field-error">{fieldError}</p>}
          </fieldset>}
        {refusal && <p role="alert" className="field-error">{refusal}</p>}
        <div className="req-actions">
          <button type="button" onClick={() => setOpen(false)} disabled={busy}>Cancel</button>
          <button type="button" onClick={() => void submit()} aria-busy={busy || undefined} disabled={busy}>{busy ? "Sending…" : "Send to These Contacts"}</button>
        </div>
        <Dialog.Close aria-label="Close dialog" className="grid-sheet-close" disabled={busy}><X aria-hidden="true" weight="regular" /></Dialog.Close>
      </Dialog.Content>
    </Dialog.Portal>
  </Dialog.Root>;
});
export default ChangeRecipient;
