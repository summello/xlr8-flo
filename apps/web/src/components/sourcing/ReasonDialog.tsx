import * as Dialog from "@radix-ui/react-dialog";
import { X } from "@phosphor-icons/react";
import { useId, useRef, useState, type ReactNode } from "react";
import type { Icon } from "@phosphor-icons/react";
import "../../styles/vendors.css";

/** What a command call came back with: null on success, else words for the dialog; `final` when the server settled it, so a retry needs a fresh key. */
export type Refusal = { message: string; final: boolean } | null;

type Props = {
  trigger: { label: string; icon: Icon };
  title: string; intro: ReactNode; rows: ReactNode[];
  keep: string; confirm: string; busy: string;
  /** Runs the command; the dialog closes when it returns null. */
  run: (reason: string, key: string) => Promise<Refusal>;
};

/** pages/sourcing.md 2 and MASTER 7.13: a destructive confirmation. The safe button takes focus, the reason is required, a refusal is shown in words and the reason is kept. */
export default function ReasonDialog({ trigger, title, intro, rows, keep, confirm, busy: busyLabel, run }: Props) {
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  const [fieldError, setFieldError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const key = useRef<string | null>(null);
  const safe = useRef<HTMLButtonElement>(null), opener = useRef<HTMLButtonElement>(null), field = useRef<HTMLTextAreaElement>(null);
  const uid = useId();

  const submit = async () => {
    if (!reason.trim()) { setFieldError("Give a reason."); field.current?.focus(); return; }
    if (reason.length > 2000) { setFieldError("Enter at most 2000 characters."); field.current?.focus(); return; }
    setFieldError(null); setMessage(null); setBusy(true);
    key.current ??= crypto.randomUUID(); // one key per attempt, reused on retry
    try {
      const refusal = await run(reason.trim(), key.current);
      if (!refusal) { setOpen(false); key.current = null; return; }
      if (refusal.final) key.current = null;
      setMessage(refusal.message);
    } catch {
      setMessage(`${title} failed. Your reason is kept. Check your connection and try again.`);
    } finally { setBusy(false); }
  };

  const Trigger = trigger.icon;
  return <Dialog.Root open={open} onOpenChange={next => { if (busy && !next) return; setOpen(next); if (next) { setReason(""); setFieldError(null); setMessage(null); key.current = null; } }}>
    <button type="button" ref={opener} className="vendor-destructive req-destructive" onClick={() => setOpen(true)}><Trigger aria-hidden="true" weight="regular" />{trigger.label}</button>
    <Dialog.Portal><Dialog.Overlay className="project-dialog-overlay" />
      <Dialog.Content className="project-dialog material-surface material-shadow req-dialog vendor-dialog" data-shadow="lg"
        onOpenAutoFocus={e => { e.preventDefault(); safe.current?.focus(); }}
        onCloseAutoFocus={e => { e.preventDefault(); opener.current?.focus(); }}
        onEscapeKeyDown={e => { if (busy) e.preventDefault(); }} onInteractOutside={e => { if (busy) e.preventDefault(); }}>
        <Dialog.Title>{title}</Dialog.Title>
        <Dialog.Description>{intro}</Dialog.Description>
        <ul className="vendor-list-plain" aria-label="What happens">{rows.map((row, i) => <li key={i}>{row}</li>)}</ul>
        <div className="req-field" data-invalid={fieldError ? true : undefined}><label className="t-label" htmlFor={`${uid}-reason`}>Reason (required)</label>
          <textarea id={`${uid}-reason`} ref={field} rows={3} value={reason} onChange={e => setReason(e.target.value)} aria-invalid={fieldError ? true : undefined} aria-describedby={fieldError ? `${uid}-error` : undefined} />
          {fieldError && <p role="alert" id={`${uid}-error`} className="field-error">{fieldError}</p>}</div>
        {message && <p role="alert" className="field-error">{message} Your reason is kept.</p>}
        <div className="req-actions">
          <button type="button" ref={safe} onClick={() => setOpen(false)} disabled={busy}>{keep}</button>
          <button type="button" className="vendor-danger-confirm" onClick={() => void submit()} aria-busy={busy || undefined} disabled={busy}>{busy ? busyLabel : confirm}</button>
        </div>
        <Dialog.Close aria-label="Close dialog" className="grid-sheet-close" disabled={busy}><X aria-hidden="true" weight="regular" /></Dialog.Close>
      </Dialog.Content>
    </Dialog.Portal>
  </Dialog.Root>;
}
