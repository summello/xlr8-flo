import * as Dialog from "@radix-ui/react-dialog";
import { Trash, X } from "@phosphor-icons/react";
import { useRef, useState } from "react";
import { failure, type Problem } from "../requisitions/api";
import "../../styles/projects.css";
import "../../styles/requisitions.css";

/** E22-S02: soft-deletes a draft after a confirm; focus starts on the safe button and Escape is ignored while busy. */
export default function DeleteDraftDialog({ kind, reference, remove, onDone }: {
  kind: string;
  reference: string;
  /** Sends the DELETE with the given Idempotency-Key; the key is kept across Try Again. */
  remove: (key: string) => Promise<{ ok: boolean; error?: Problem }>;
  onDone: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const key = useRef<string | null>(null);
  const keep = useRef<HTMLButtonElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);

  const confirm = async () => {
    key.current ??= crypto.randomUUID(); // one key for this attempt, reused on Try Again
    setBusy(true); setMessage(null);
    try {
      const result = await remove(key.current);
      if (result.ok) { setOpen(false); key.current = null; onDone(); return; }
      setMessage(failure(result.error, "Deleting the draft"));
    } catch {
      setMessage(failure(undefined, "Deleting the draft"));
    }
    setBusy(false);
  };

  return <Dialog.Root open={open} onOpenChange={next => { if (busy && !next) return; setOpen(next); if (next) setMessage(null); }}>
    <button type="button" ref={trigger} className="req-destructive" onClick={() => setOpen(true)}><Trash aria-hidden="true" weight="regular" />Delete draft</button>
    <Dialog.Portal><Dialog.Overlay className="project-dialog-overlay" />
      <Dialog.Content role="alertdialog" className="project-dialog material-surface material-shadow req-dialog" data-shadow="lg"
        onOpenAutoFocus={e => { e.preventDefault(); keep.current?.focus(); }}
        onCloseAutoFocus={e => { e.preventDefault(); trigger.current?.focus(); }}
        onEscapeKeyDown={e => { if (busy) e.preventDefault(); }} onInteractOutside={e => { if (busy) e.preventDefault(); }}>
        <Dialog.Title>{message ? "Not deleted" : `Delete ${kind} draft?`}</Dialog.Title>
        <Dialog.Description>{message ? `Nothing changed. ${message}` : `${reference} is hidden from every list. Its number is never reused.`}</Dialog.Description>
        <div className="req-actions">
          <button type="button" ref={keep} onClick={() => setOpen(false)} disabled={busy}>Keep Draft</button>
          <button type="button" className="req-destructive" onClick={() => void confirm()} aria-busy={busy || undefined} disabled={busy}>{busy ? "Deleting…" : message ? "Try Again" : "Delete Draft"}</button>
        </div>
        <Dialog.Close aria-label="Close dialog" className="grid-sheet-close" disabled={busy}><X aria-hidden="true" weight="regular" /></Dialog.Close>
      </Dialog.Content>
    </Dialog.Portal>
  </Dialog.Root>;
}
