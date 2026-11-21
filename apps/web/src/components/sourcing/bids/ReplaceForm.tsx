import { useRef, useState } from "react";
import { apiClient } from "../../../api/client";
import { failure, mutationHeaders, type Problem } from "../../requisitions/api";
import type { Rfq } from "../api";
import { isFull, sentInstant, type AnyBid } from "./api";

/** Before the deadline a submitted bid is corrected by a replacement: a new sent time and a reason; the original is never altered (SRC-010). */
export default function ReplaceForm({ rfq, bid, onReplaced }: { rfq: Rfq; bid: AnyBid; onReplaced: (next: AnyBid) => void }) {
  const zone = rfq.deadline_tz ?? "UTC";
  const [open, setOpen] = useState(false);
  const [date, setDate] = useState(""), [time, setTime] = useState(""), [reason, setReason] = useState("");
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const key = useRef<string | null>(null);

  const send = async () => {
    const found: Record<string, string> = {}, sent = sentInstant(date, time, zone);
    if ("error" in sent) found.sent = sent.error;
    if (reason.trim().length < 1 || reason.trim().length > 500) found.reason = "Enter a reason of 1 to 500 characters.";
    setErrors(found); setMessage(null);
    if (Object.keys(found).length > 0 || "error" in sent) return;
    key.current ??= crypto.randomUUID();
    setBusy(true);
    try {
      const { data, error, response } = await apiClient.POST("/api/v1/sourcing/rfqs/{id}/bids/{bid_id}/replace", { params: { path: { id: rfq.id, bid_id: bid.id } }, headers: mutationHeaders(key.current), body: { reason: reason.trim(), sent_at: sent.iso } });
      if (data && isFull(data)) { onReplaced(data); return; }
      key.current = null;
      const p = error as Problem | undefined, code = (p?.checks as { problem?: string } | undefined)?.problem;
      setMessage(code === "deadline_passed" ? "Bids can no longer be replaced after the bidding stop time." : failure(p, "Replacing the bid"));
      if (response?.status === 422 && p?.errors?.[0]) setErrors({ sent: p.errors[0].message });
    } catch { setMessage(failure(undefined, "Replacing the bid")); } finally { setBusy(false); }
  };

  if (!open) return <div className="req-actions"><button type="button" onClick={() => setOpen(true)}>Replace</button></div>;
  return <form className="bid-replace" noValidate onSubmit={e => { e.preventDefault(); void send(); }}>
    <h3>Replace This Bid</h3>
    <p className="req-sub">The replacement is a new draft. Every price and answer is keyed again; this bid stays on record.</p>
    <div className="req-fields">
      <div className="req-field" data-invalid={errors.sent ? "true" : undefined}><label className="t-label" htmlFor="replace-date">Vendor Sent It On (date)</label>
        <input id="replace-date" value={date} onChange={e => setDate(e.target.value)} inputMode="numeric" placeholder="YYYY-MM-DD" aria-invalid={!!errors.sent || undefined} aria-describedby={errors.sent ? "replace-sent-error" : undefined} /></div>
      <div className="req-field" data-invalid={errors.sent ? "true" : undefined}><label className="t-label" htmlFor="replace-time">Time (24-hour, {zone})</label>
        <input id="replace-time" value={time} onChange={e => setTime(e.target.value)} inputMode="numeric" placeholder="HH:MM" aria-invalid={!!errors.sent || undefined} aria-describedby={errors.sent ? "replace-sent-error" : undefined} />
        {errors.sent && <p id="replace-sent-error" className="field-error">{errors.sent}</p>}</div>
    </div>
    <div className="req-field" data-invalid={errors.reason ? "true" : undefined}><label className="t-label" htmlFor="replace-reason">Reason for Replacing</label>
      <textarea id="replace-reason" rows={2} maxLength={500} value={reason} onChange={e => setReason(e.target.value)} aria-invalid={!!errors.reason || undefined} aria-describedby={errors.reason ? "replace-reason-error" : undefined} />
      {errors.reason && <p id="replace-reason-error" className="field-error">{errors.reason}</p>}</div>
    {message && <p role="alert" className="field-error">{message} Your entries are kept.</p>}
    <div className="req-actions"><button type="button" disabled={busy} onClick={() => setOpen(false)}>Keep This Bid</button><button type="submit" disabled={busy} aria-busy={busy || undefined}>Replace Bid</button></div>
  </form>;
}
