import * as Dialog from "@radix-ui/react-dialog";
import { X } from "@phosphor-icons/react";
import { useId, useRef, useState } from "react";
import { apiClient } from "../../../api/client";
import { failure, mutationHeaders, type Problem } from "../../requisitions/api";
import type { Po } from "../api";
import "../../../styles/vendors.css";
import "../../../styles/projects.css";

/** E13-S27: close an issued PO (terminal, moves no money). Shown only when the server lists `close` in `actions`. */
export default function ClosePo({ po, onDone }: { po: Po; onDone: () => void }) {
  const [open, setOpen] = useState(false), [busy, setBusy] = useState(false);
  const [reason, setReason] = useState(""), [fieldError, setFieldError] = useState<string | null>(null);
  const [refusal, setRefusal] = useState<{ message: string; stale: boolean } | null>(null);
  const key = useRef<string | null>(null);
  const safe = useRef<HTMLButtonElement>(null), opener = useRef<HTMLButtonElement>(null), field = useRef<HTMLTextAreaElement>(null);
  const uid = useId();
  if (!po.actions?.includes("close")) return null;

  const submit = async () => {
    const text = reason.trim();
    if (!text) { setFieldError("Give a reason."); field.current?.focus(); return; }
    setFieldError(null); setRefusal(null); setBusy(true);
    key.current ??= crypto.randomUUID(); // one key per attempt, reused on a network retry
    try {
      const { response, error } = await apiClient.POST("/api/v1/purchase-orders/{id}/transitions", { params: { path: { id: po.id } }, headers: mutationHeaders(key.current, po.version), body: { action: "close", reason: text } });
      if (response.ok) { key.current = null; setOpen(false); onDone(); return; }
      const problem = error as (Problem & { checks?: { problem?: string } }) | undefined;
      if (response.status === 409 || response.status === 403 || response.status === 422) key.current = null;
      const stale = response.status === 409 && problem?.checks?.problem === "stale_version";
      setRefusal({ message: stale ? "This order changed since you opened it." : failure(problem, "Closing the order"), stale });
    } catch {
      setRefusal({ message: "Closing the order failed. Check your connection and try again.", stale: false });
    } finally { setBusy(false); }
  };

  return <Dialog.Root open={open} onOpenChange={next => { if (busy && !next) return; setOpen(next); }}>
    <button type="button" ref={opener} className="req-link-button" onClick={() => { setReason(""); setFieldError(null); setRefusal(null); key.current = null; setOpen(true); }}>Close Purchase Order</button>
    <Dialog.Portal><Dialog.Overlay className="project-dialog-overlay" />
      <Dialog.Content className="project-dialog material-surface material-shadow req-dialog vendor-dialog" data-shadow="lg"
        onOpenAutoFocus={e => { e.preventDefault(); safe.current?.focus(); }}
        onCloseAutoFocus={e => { e.preventDefault(); opener.current?.focus(); }}
        onEscapeKeyDown={e => { if (busy) e.preventDefault(); }} onInteractOutside={e => { if (busy) e.preventDefault(); }}>
        <Dialog.Title>Close {po.number}</Dialog.Title>
        <Dialog.Description>Closing ends the order. It cannot be reopened.</Dialog.Description>
        <div className="req-field" data-invalid={fieldError ? true : undefined}><label className="t-label" htmlFor={`${uid}-reason`}>Reason (required)</label>
          <textarea id={`${uid}-reason`} ref={field} rows={3} maxLength={500} value={reason} onChange={e => setReason(e.target.value)} aria-invalid={fieldError ? true : undefined} aria-describedby={fieldError ? `${uid}-error` : undefined} />
          {fieldError && <p role="alert" id={`${uid}-error`} className="field-error">{fieldError}</p>}</div>
        {refusal && <p role="alert" className="field-error">{refusal.message} Your reason is kept.{refusal.stale && <> <button type="button" onClick={() => { setOpen(false); onDone(); }}>Reload</button></>}</p>}
        <div className="req-actions">
          <button type="button" ref={safe} onClick={() => setOpen(false)} disabled={busy}>Keep Open</button>
          <button type="button" onClick={() => void submit()} aria-busy={busy || undefined} disabled={busy}>{busy ? "Closing…" : "Close Order"}</button>
        </div>
        <Dialog.Close aria-label="Close dialog" className="grid-sheet-close" disabled={busy}><X aria-hidden="true" weight="regular" /></Dialog.Close>
      </Dialog.Content>
    </Dialog.Portal>
  </Dialog.Root>;
}
