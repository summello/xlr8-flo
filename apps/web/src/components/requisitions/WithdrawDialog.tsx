import * as Dialog from "@radix-ui/react-dialog";
import { Prohibit, X } from "@phosphor-icons/react";
import { useEffect, useRef, useState } from "react";
import { apiClient } from "../../api/client";
import { formatMoney } from "../../lib/money";
import { failure, mutationHeaders, type Problem, type Requisition } from "./api";

type Phase = "ask" | "busy" | "failed" | "decided";
const COPY = {
  withdraw: { stops: (row: Requisition, approvers: string[]) => row.status === "draft" ? `${row.number} stops here; it was never sent for approval.` : `The approval and any sourcing work for ${row.number} stop${approvers.length ? `, and ${approvers.join(", ")} ${approvers.length === 1 ? "is" : "are"} told` : ""}.`, trigger: "Withdraw requisition", title: "Withdraw requisition", confirm: "Withdraw Requisition", busy: "Withdrawing…", status: "Withdrawn", to: "withdrawn" as const, failed: "Not withdrawn" },
  cancel: { stops: (row: Requisition, approvers: string[]) => `An administrator cancels ${row.number}${approvers.length ? `, and ${approvers.join(", ")} ${approvers.length === 1 ? "is" : "are"} told` : ""}.`, trigger: "Cancel requisition", title: "Cancel requisition", confirm: "Cancel Requisition", busy: "Cancelling…", status: "Cancelled", to: "cancelled" as const, failed: "Not cancelled" },
};

/** approval-inbox.md section 3: an alertdialog whose first focus is the safe button, with a busy state that ignores Escape. */
export default function WithdrawDialog({ row, mode, approvers, onDone, onStale }: { row: Requisition; mode: "withdraw" | "cancel"; approvers: string[]; onDone: (next: Requisition) => void; onStale: () => void }) {
  const copy = COPY[mode];
  const [open, setOpen] = useState(false);
  const [phase, setPhase] = useState<Phase>("ask");
  const [reason, setReason] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const key = useRef<string | null>(null);
  const keep = useRef<HTMLButtonElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const busy = phase === "busy";
  useEffect(() => { if (phase === "decided") keep.current?.focus(); }, [phase]);
  const held = row.reservation && row.reservation.remaining !== "0.0000" ? row.reservation : null;

  const confirm = async () => {
    key.current ??= crypto.randomUUID(); // one key for this attempt, reused on Try Again
    setPhase("busy"); setMessage(null);
    try {
      const { data, error, response } = await apiClient.POST("/api/v1/requisitions/{id}/transitions", { params: { path: { id: row.id } }, headers: mutationHeaders(key.current, row.version), body: { to: copy.to, ...(reason.trim() ? { reason: reason.trim() } : {}) } });
      if (data) { setOpen(false); setPhase("ask"); key.current = null; onDone(data); return; }
      const problem = error as Problem | undefined;
      if (response.status === 409 && problem?.checks?.problem === "invalid_transition") { setPhase("decided"); return; }
      setMessage(failure(problem, mode === "withdraw" ? "Withdrawing" : "Cancelling")); setPhase("failed");
    } catch {
      setMessage(failure(undefined, mode === "withdraw" ? "Withdrawing" : "Cancelling")); setPhase("failed");
    }
  };

  return <Dialog.Root open={open} onOpenChange={next => { if (busy && !next) return; setOpen(next); if (next) { setPhase("ask"); setMessage(null); } }}>
    <button type="button" ref={trigger} className="req-destructive" onClick={() => setOpen(true)}><Prohibit aria-hidden="true" weight="regular" />{copy.trigger}</button>
    <Dialog.Portal><Dialog.Overlay className="project-dialog-overlay" />
      <Dialog.Content role="alertdialog" className="project-dialog material-surface material-shadow req-dialog" data-shadow="lg"
        onOpenAutoFocus={e => { e.preventDefault(); keep.current?.focus(); }}
        onCloseAutoFocus={e => { e.preventDefault(); trigger.current?.focus(); }}
        onEscapeKeyDown={e => { if (busy) e.preventDefault(); }} onInteractOutside={e => { if (busy) e.preventDefault(); }}>
        {phase === "decided" ? <>
          <Dialog.Title>Already Approved</Dialog.Title>
          <Dialog.Description>{row.number} moved on before this request arrived, so nothing was withdrawn and nothing was released.</Dialog.Description>
          <div className="req-actions"><button type="button" ref={keep} onClick={() => { setOpen(false); onStale(); }}>Close</button></div>
        </> : <>
          <Dialog.Title>{phase === "failed" ? copy.failed : `${copy.title}?`}</Dialog.Title>
          <Dialog.Description>{phase === "failed" ? `Nothing changed. ${message ?? ""}` : copy.stops(row, approvers)}</Dialog.Description>
          {phase !== "failed" && <ul className="req-consequences">
            <li>{copy.stops(row, approvers)}</li>
            <li>{held ? `Releases ${formatMoney(held.remaining, held.currency)} ${held.currency}${row.project_name ? ` to ${row.project_name}` : ""}, exactly once.` : "Nothing is reserved, so nothing is released."}</li>
            <li>Status becomes {copy.status}. It cannot be reopened.</li></ul>}
          <div className="req-field"><label className="t-label" htmlFor="withdraw-reason">Reason (optional)</label>
            <textarea id="withdraw-reason" rows={3} maxLength={1000} value={reason} onChange={e => setReason(e.target.value)} aria-describedby="withdraw-reason-help" />
            <p className="field-help" id="withdraw-reason-help">Shown to the approver. Leave it empty if there is nothing to add.</p></div>
          {phase === "failed" && <p role="alert" className="field-error">{message} Your reason is kept.</p>}
          <div className="req-actions">
            <button type="button" ref={keep} onClick={() => setOpen(false)} disabled={busy}>Keep Requisition</button>
            <button type="button" className="req-destructive" onClick={() => void confirm()} aria-busy={busy || undefined} disabled={busy}>{busy ? copy.busy : phase === "failed" ? "Try Again" : copy.confirm}</button>
          </div>
        </>}
        <Dialog.Close aria-label="Close dialog" className="grid-sheet-close" disabled={busy}><X aria-hidden="true" weight="regular" /></Dialog.Close>
      </Dialog.Content>
    </Dialog.Portal>
  </Dialog.Root>;
}
