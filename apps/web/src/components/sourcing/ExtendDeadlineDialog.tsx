import * as Dialog from "@radix-ui/react-dialog";
import { CalendarPlus, X } from "@phosphor-icons/react";
import { useRef, useState } from "react";
import { apiClient } from "../../api/client";
import { failure, formatDateTime, mutationHeaders, type Problem } from "../requisitions/api";
import { instantIn, wallClock, type Rfq } from "./api";

/** pages/sourcing.md 4.9: date and 24-hour time in the RFQ's zone; later than the current deadline, never equal. */
export default function ExtendDeadlineDialog({ rfq, onDone }: { rfq: Rfq; onDone: (next: Rfq) => void }) {
  const zone = rfq.deadline_tz ?? "UTC";
  const start = wallClock(rfq.deadline_at, zone);
  const [open, setOpen] = useState(false);
  const [date, setDate] = useState(start.date);
  const [time, setTime] = useState(start.time);
  const [reason, setReason] = useState("");
  const [error, setError] = useState<{ field: "date" | "time" | "reason"; text: string } | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const key = useRef<string | null>(null);
  const keep = useRef<HTMLButtonElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const dateRef = useRef<HTMLInputElement>(null), timeRef = useRef<HTMLInputElement>(null), reasonRef = useRef<HTMLTextAreaElement>(null);
  const refuse = (field: "date" | "time" | "reason", text: string) => { setError({ field, text }); (field === "date" ? dateRef : field === "time" ? timeRef : reasonRef).current?.focus(); };

  const submit = async () => {
    setMessage(null);
    const instant = instantIn(date, time, zone);
    if (!instant) return refuse(/^\d{4}-\d{2}-\d{2}$/.test(date) ? "time" : "date", "Enter a date as YYYY-MM-DD and a 24-hour time as HH:MM.");
    // Strictly later: the server refuses an equal instant as well as an earlier one.
    if (instant.getTime() <= new Date(rfq.deadline_at).getTime()) return refuse("date", `Choose a deadline later than the current one, ${formatDateTime(rfq.deadline_at)}.`);
    if (reason.length > 500) return refuse("reason", "Enter at most 500 characters.");
    key.current ??= crypto.randomUUID(); // one key per attempt, reused on retry
    setBusy(true);
    try {
      const { data, error: problem, response } = await apiClient.POST("/api/v1/sourcing/rfqs/{id}/extend-deadline", { params: { path: { id: rfq.id } }, headers: mutationHeaders(key.current), body: { deadline_at: instant.toISOString(), deadline_tz: zone, ...(reason.trim() ? { reason: reason.trim() } : {}) } });
      if (data) { setOpen(false); key.current = null; onDone(data); return; }
      const p = problem as Problem | undefined;
      if (response.status === 409 || response.status === 422) { key.current = null; refuse("date", p?.errors?.[0]?.message ?? p?.detail ?? "That deadline was refused."); }
      else setMessage(failure(p, "Extending the deadline"));
    } catch {
      setMessage(failure(undefined, "Extending the deadline"));
    } finally { setBusy(false); }
  };

  const invalid = (field: string) => error?.field === field || undefined;
  return <Dialog.Root open={open} onOpenChange={next => { if (busy && !next) return; setOpen(next); if (next) { const w = wallClock(rfq.deadline_at, zone); setDate(w.date); setTime(w.time); setReason(""); setError(null); setMessage(null); key.current = null; } }}>
    <button type="button" ref={trigger} onClick={() => setOpen(true)}><CalendarPlus aria-hidden="true" weight="regular" />Extend Deadline</button>
    <Dialog.Portal><Dialog.Overlay className="project-dialog-overlay" />
      <Dialog.Content className="project-dialog material-surface material-shadow req-dialog" data-shadow="lg"
        onOpenAutoFocus={e => { e.preventDefault(); keep.current?.focus(); }}
        onCloseAutoFocus={e => { e.preventDefault(); trigger.current?.focus(); }}
        onEscapeKeyDown={e => { if (busy) e.preventDefault(); }} onInteractOutside={e => { if (busy) e.preventDefault(); }}>
        <Dialog.Title>Extend Deadline</Dialog.Title>
        <Dialog.Description>{rfq.number} closes {formatDateTime(rfq.deadline_at)} ({zone}). A deadline can be extended, never brought forward. Every invitee is told of the new version.</Dialog.Description>
        <div className="req-fields">
          <div className="req-field" data-invalid={invalid("date")}><label className="t-label" htmlFor="extend-date">New Date</label>
            <input id="extend-date" ref={dateRef} value={date} onChange={e => setDate(e.target.value)} inputMode="numeric" placeholder="YYYY-MM-DD" aria-invalid={invalid("date")} aria-describedby={error?.field === "date" ? "extend-error" : undefined} />
            {error?.field === "date" && <p role="alert" id="extend-error" className="field-error">{error.text}</p>}</div>
          <div className="req-field" data-invalid={invalid("time")}><label className="t-label" htmlFor="extend-time">New Time (24-hour, {zone})</label>
            <input id="extend-time" ref={timeRef} value={time} onChange={e => setTime(e.target.value)} inputMode="numeric" placeholder="HH:MM" aria-invalid={invalid("time")} aria-describedby={error?.field === "time" ? "extend-error-time" : undefined} />
            {error?.field === "time" && <p role="alert" id="extend-error-time" className="field-error">{error.text}</p>}</div>
        </div>
        <div className="req-field" data-invalid={invalid("reason")}><label className="t-label" htmlFor="extend-reason">Reason (optional)</label>
          <textarea id="extend-reason" ref={reasonRef} rows={3} value={reason} onChange={e => setReason(e.target.value)} aria-invalid={invalid("reason")} aria-describedby={error?.field === "reason" ? "extend-error-reason" : undefined} />
          {error?.field === "reason" && <p role="alert" id="extend-error-reason" className="field-error">{error.text}</p>}</div>
        {message && <p role="alert" className="field-error">{message} Your entries are kept.</p>}
        <div className="req-actions">
          <button type="button" ref={keep} onClick={() => setOpen(false)} disabled={busy}>Keep Current Deadline</button>
          <button type="button" onClick={() => void submit()} aria-busy={busy || undefined} disabled={busy}>{busy ? "Extending…" : "Extend Deadline"}</button>
        </div>
        <Dialog.Close aria-label="Close dialog" className="grid-sheet-close" disabled={busy}><X aria-hidden="true" weight="regular" /></Dialog.Close>
      </Dialog.Content>
    </Dialog.Portal>
  </Dialog.Root>;
}
