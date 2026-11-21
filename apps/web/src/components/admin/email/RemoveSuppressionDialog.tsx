import * as Dialog from "@radix-ui/react-dialog";
import { X } from "@phosphor-icons/react";
import { useId, useRef, useState } from "react";
import { apiClient } from "../../../api/client";
import { writeHeaders } from "../../../features/auth/api";
import { failure, type Problem } from "../../requisitions/api";
import type { Suppression } from "./api";
import "../../../styles/vendors.css";
import "../../../styles/projects.css";

type Props = { row: Suppression; opener: HTMLElement | null; onClose: () => void; onRemoved: (gone: boolean) => void };

/** The CancelOrReduce pattern: safe button focused on open, Escape ignored while busy, reason required. */
export default function RemoveSuppressionDialog({ row, opener, onClose, onRemoved }: Props) {
  const [reason, setReason] = useState(""), [fieldError, setFieldError] = useState<string | null>(null);
  const [refusal, setRefusal] = useState<string | null>(null), [busy, setBusy] = useState(false);
  const safe = useRef<HTMLButtonElement>(null), field = useRef<HTMLTextAreaElement>(null);
  const uid = useId();

  const confirm = async () => {
    if (!reason.trim()) { setFieldError("Give a reason."); field.current?.focus(); return; }
    setFieldError(null); setRefusal(null); setBusy(true);
    try {
      const { response, error } = await apiClient.DELETE("/api/v1/admin/email-suppressions/{id}", {
        params: { path: { id: row.id } }, body: { reason: reason.trim() }, headers: writeHeaders(),
      });
      if (response.ok) onRemoved(false);
      else if (response.status === 404) onRemoved(true);
      else setRefusal(failure(error as Problem | undefined, "Removing the address"));
    } catch {
      setRefusal("Removing the address failed. Your reason is kept. Check your connection and try again.");
    } finally { setBusy(false); }
  };

  return <Dialog.Root open onOpenChange={next => { if (!next && !busy) onClose(); }}>
    <Dialog.Portal><Dialog.Overlay className="project-dialog-overlay" />
      <Dialog.Content role="alertdialog" className="project-dialog material-surface material-shadow req-dialog vendor-dialog" data-shadow="lg"
        onOpenAutoFocus={e => { e.preventDefault(); safe.current?.focus(); }}
        onCloseAutoFocus={e => { e.preventDefault(); opener?.focus(); }}
        onEscapeKeyDown={e => { if (busy) e.preventDefault(); }} onInteractOutside={e => { if (busy) e.preventDefault(); }}>
        <Dialog.Title>Remove {row.email} from the suppression list?</Dialog.Title>
        <Dialog.Description>Purchase order emails to this address will be sent again. The removal and your reason are recorded.</Dialog.Description>
        <div className="req-field" data-invalid={fieldError ? true : undefined}>
          <label className="t-label" htmlFor={`${uid}-reason`}>Reason</label>
          <textarea id={`${uid}-reason`} ref={field} rows={3} maxLength={500} value={reason} onChange={e => setReason(e.target.value)}
            aria-invalid={fieldError ? true : undefined} aria-describedby={`${uid}-help${fieldError ? ` ${uid}-error` : ""}`} />
          <p id={`${uid}-help`}>Why the address can receive email again, for example the mailbox was fixed.</p>
          {fieldError && <p role="alert" id={`${uid}-error`} className="field-error">{fieldError}</p>}
        </div>
        {refusal && <p role="alert" className="field-error">{refusal} Your reason is kept.</p>}
        <div className="req-actions">
          <button type="button" ref={safe} onClick={onClose} disabled={busy}>Keep Address</button>
          <button type="button" className="vendor-danger-confirm" onClick={() => void confirm()} aria-busy={busy || undefined}>{busy ? "Removing…" : "Remove Address"}</button>
        </div>
        <Dialog.Close aria-label="Close dialog" className="grid-sheet-close" disabled={busy}><X aria-hidden="true" weight="regular" /></Dialog.Close>
      </Dialog.Content>
    </Dialog.Portal>
  </Dialog.Root>;
}
