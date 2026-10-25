import * as Dialog from "@radix-ui/react-dialog";
import { CheckCircle, Warning, X } from "@phosphor-icons/react";
import { useRef, useState } from "react";
import { apiClient } from "../../../api/client";
import { writeHeaders } from "../../../features/auth/api";
import { formatQuantity } from "../../../lib/decimal";
import { formatMoney } from "../../../lib/money";
import { restoreFocus } from "../../../lib/focus";
import { failure, formatDate, formatDateTime, mutationHeaders, type Problem } from "../../requisitions/api";
import { wallClock, type Invitee, type Rfq } from "../api";
import { isFull, isLate, parseAmount, sentInstant, type AnyBid, type Bid } from "./api";
import Person from "../../ui/Person";
import ReplaceForm from "./ReplaceForm";

type Props = { rfq: Rfq; invitee: Invitee; bid: AnyBid | undefined; opener: HTMLElement | null; onClose: () => void; onChanged: () => void };
type Errors = Record<string, string>;

const LATE = "The vendor sent this bid after the bidding stop time (by Vendor Sent It On, not Keyed By Us).";
const base = (rfqId: string) => ({ path: { id: rfqId } });

/** pages/sourcing.md 4.5: the bid sheet. Keyed by the buyer from the vendor's paper, in the vendor's currency (SRC-006). */
export default function BidSheet(props: Props) {
  const [bid, setBid] = useState<AnyBid | undefined>(props.bid);
  const [epoch, setEpoch] = useState(0);
  // Opening another bid (a replacement, or along the chain) remounts the form so it starts from that bid, not the last one.
  return <KeyingSheet key={epoch} {...props} bid={bid} setBid={setBid} switchTo={next => { setBid(next); setEpoch(n => n + 1); }} />;
}

function KeyingSheet({ rfq, invitee, bid, setBid, switchTo, opener, onClose, onChanged }: Omit<Props, "bid"> & { bid: AnyBid | undefined; setBid: (bid: AnyBid) => void; switchTo: (bid: AnyBid) => void }) {
  const zone = rfq.deadline_tz ?? "UTC";
  const keying = !bid || (bid.status === "draft" && isFull(bid));
  const full: Bid | undefined = bid && isFull(bid) ? bid : undefined;
  const sentAt = full ? wallClock(full.sent_at, zone) : { date: "", time: "" };
  const [version, setVersion] = useState(String(full?.rfq_version ?? invitee.latest_sent_version ?? rfq.version));
  const [currency, setCurrency] = useState(full?.currency ?? rfq.currency);
  const [date, setDate] = useState(sentAt.date), [time, setTime] = useState(sentAt.time);
  const [file, setFile] = useState<File | null>(null);
  const [freight, setFreight] = useState(full ? formatQuantity(full.freight) : "");
  const [leadTime, setLeadTime] = useState(full?.lead_time_days?.toString() ?? "");
  const [validUntil, setValidUntil] = useState(full?.valid_until ?? "");
  const [terms, setTerms] = useState(full?.delivery_terms ?? ""), [warranty, setWarranty] = useState(full?.warranty ?? ""), [exceptions, setExceptions] = useState(full?.exceptions ?? "");
  const open = rfq.requirements?.filter(q => !q.waived) ?? [];
  const [lines, setLines] = useState(() => rfq.lines.map(l => { const b = full?.lines.find(x => x.rfq_line_id === l.id); const quoted = b ? b.quoted : true; return { quoted, price: b?.unit_price ? formatQuantity(b.unit_price) : "", tax: b?.quoted ? formatQuantity(b.tax) : "" }; }));
  const [answers, setAnswers] = useState(() => open.map(q => { const a = full?.requirements.find(x => x.requirement_id === q.id); return { met: a ? (a.met ? "met" : "not") : "", note: a?.note ?? "" }; }));
  const [errors, setErrors] = useState<Errors>({});
  const [message, setMessage] = useState<string | null>(null);
  const [late, setLate] = useState(false);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const keys = useRef<Record<string, string>>({});
  const lead = !!rfq.can_edit && !!rfq.can_approve;
  const [now] = useState(() => Date.now());
  const passed = new Date(rfq.deadline_at).getTime() <= now;
  const stale = (name: string) => (keys.current[name] ??= crypto.randomUUID());
  const fresh = (name: string) => { delete keys.current[name]; };
  const lineError = (i: number) => errors[`lines/${rfq.lines[i]!.id}`];

  /** Everything the buyer typed, checked at the boundary. The server checks it again. */
  const gather = () => {
    const found: Errors = {};
    const sent = sentInstant(date, time, zone);
    if ("error" in sent) found.sent = sent.error;
    if (!/^[A-Z]{3}$/.test(currency)) found.currency = "Enter the three-letter currency code, such as USD.";
    const out: Record<string, unknown>[] = rfq.lines.map((l, i) => {
      const line = lines[i]!;
      if (!line.quoted) return { rfq_line_id: l.id, quoted: false };
      const price = parseAmount(line.price), tax = line.tax.trim() === "" ? undefined : parseAmount(line.tax);
      if (price === null || !/[1-9]/.test(price)) found[`lines/${l.id}`] = "Enter the unit price, or mark this line Not Quoted.";
      else if (line.tax.trim() === "") found[`lines/${l.id}`] = "Enter the tax on this line, or 0 if none.";
      else if (tax === null) found[`lines/${l.id}`] = "Enter the tax as an amount with at most 4 decimals.";
      return { rfq_line_id: l.id, quoted: true, unit_price: price, tax };
    });
    const money = freight.trim() === "" ? "0" : parseAmount(freight);
    if (money === null) found.freight = "Enter freight as an amount with at most 4 decimals.";
    if (!file && !full?.evidence_attachment_id) found.evidence = "Attach the vendor's email or file as evidence.";
    if (leadTime.trim() !== "" && !/^\d{1,5}$/.test(leadTime.trim())) found.lead = "Enter lead time as a whole number of days.";
    return { found, sent, out, money };
  };

  /** Create on first save, upload the evidence, then PATCH everything. Returns the saved bid or null. */
  const save = async (): Promise<Bid | null> => {
    const { found, sent, out, money } = gather();
    setErrors(found); setMessage(null);
    if (Object.keys(found).length > 0 || "error" in sent) return null;
    let current = full;
    if (!current) {
      const { data, error, response } = await apiClient.POST("/api/v1/sourcing/rfqs/{id}/bids", { params: base(rfq.id), headers: mutationHeaders(stale("create")), body: { vendor_id: invitee.vendor_id, rfq_version: Number(version), currency, sent_at: sent.iso } });
      if (!data || !isFull(data)) { report(error as Problem | undefined, response?.status, "Saving the bid"); return null; }
      current = data; setBid(data);
    }
    let evidence = current.evidence_attachment_id ?? undefined;
    if (file) {
      const body = new FormData(); body.set("file", file);
      const upload = await fetch(`/api/v1/documents/bid/${current.id}/attachments`, { method: "POST", credentials: "same-origin", headers: writeHeaders(), body });
      if (upload.status === 403) { setErrors({ evidence: "You can't attach files on this RFQ. Ask an administrator for record access." }); return null; }
      if (!upload.ok) { setErrors({ evidence: failure(await upload.json().catch(() => undefined) as { detail?: string } | undefined, "Uploading the file") }); return null; }
      evidence = ((await upload.json()) as { id: string }).id; setFile(null);
    }
    const body = {
      sent_at: sent.iso, currency, rfq_version: Number(version), freight: money!, lines: out as never, ...(evidence ? { evidence_attachment_id: evidence } : {}),
      ...(leadTime.trim() ? { lead_time_days: Number(leadTime) } : { lead_time_days: null }), valid_until: validUntil || null,
      delivery_terms: terms || null, warranty: warranty || null, exceptions: exceptions || null,
      requirements: answers.flatMap((a, i) => a.met ? [{ requirement_id: open[i]!.id, met: a.met === "met", note: a.note.trim() || null }] : []),
    };
    const { data, error, response } = await apiClient.PATCH("/api/v1/sourcing/rfqs/{id}/bids/{bid_id}", { params: { path: { id: rfq.id, bid_id: current.id } }, headers: mutationHeaders(crypto.randomUUID(), current.row_version), body });
    if (!data || !isFull(data)) { report(error as Problem | undefined, response?.status, "Saving the bid"); return null; }
    setBid(data); onChanged();
    return data;
  };

  /** Field problems land on their fields; anything else is one message that says the entries are kept (UX-005). */
  const report = (problem: Problem | undefined, status: number | undefined, action: string) => {
    const mapped: Errors = {};
    for (const e of problem?.errors ?? []) mapped[e.field === "evidence_attachment_id" ? "evidence" : e.field] = e.message;
    if (status === 409 && (problem?.checks as { problem?: string } | undefined)?.problem === "stale_version") setMessage("Someone changed this bid since you opened it. Close the sheet and open it again; your entries here are kept until then.");
    else if (Object.keys(mapped).length > 0) setErrors(mapped);
    else setMessage(failure(problem, action));
  };

  const submit = async (accepting: boolean) => {
    if (accepting && (reason.trim().length < 1 || reason.trim().length > 500)) { setErrors({ reason: "Enter a reason of 1 to 500 characters." }); return; }
    setBusy(true);
    try {
      const saved = keying ? await save() : full;
      if (!saved && keying) return;
      const target = saved ?? bid!;
      const { data, error, response } = await apiClient.POST("/api/v1/sourcing/rfqs/{id}/bids/{bid_id}/submit", { params: { path: { id: rfq.id, bid_id: target.id } }, headers: mutationHeaders(stale(`submit-${target.id}-${accepting}`), target.row_version), body: accepting ? { accept_late_reason: reason.trim() } : {} });
      if (data) { setBid(data); setLate(false); onChanged(); return; }
      fresh(`submit-${target.id}-${accepting}`);
      const p = error as Problem | undefined;
      if (response?.status === 409 && (p?.checks as { problem?: string } | undefined)?.problem === "late_bid") { setLate(true); return; }
      report(p, response?.status, "Submitting the bid");
    } catch { setMessage(failure(undefined, "Submitting the bid")); } finally { setBusy(false); }
  };

  /** Follow the replacement chain: the original is never altered, so both stay readable (SRC-010). */
  const follow = (id: string) => run(async () => {
    const { data, error, response } = await apiClient.GET("/api/v1/sourcing/rfqs/{id}/bids/{bid_id}", { params: { path: { id: rfq.id, bid_id: id } } });
    if (data) switchTo(data); else report(error as Problem | undefined, response?.status, "Opening the bid");
  });
  const run = async (work: () => Promise<unknown>) => { setBusy(true); try { await work(); } catch { setMessage(failure(undefined, "Saving the bid")); } finally { setBusy(false); } };
  const problems = Object.entries(errors);
  const set = <T,>(list: T[], i: number, patch: Partial<T>) => list.map((x, n) => n === i ? { ...x, ...patch } : x);
  const vendor = String(invitee.vendor_snapshot.trading_name ?? "Vendor");
  const showTotals = full?.totals;

  return <Dialog.Root open onOpenChange={next => { if (!next && !busy) onClose(); }}>
    <Dialog.Portal><Dialog.Overlay className="appr-scrim" />
      <Dialog.Content className="appr-sheet bid-sheet" aria-describedby="bid-sheet-desc" onCloseAutoFocus={e => { e.preventDefault(); restoreFocus(opener); }}>
        <header className="appr-sheet-head">
          <div><Dialog.Title>Record Bid · {vendor}</Dialog.Title>
            <Dialog.Description id="bid-sheet-desc" className="req-sub">{rfq.number} · RFQ version answered {version} · amounts are in the vendor's currency, {currency}</Dialog.Description></div>
          <Dialog.Close className="appr-close" aria-label="Close" disabled={busy}><X aria-hidden="true" />Close</Dialog.Close>
        </header>
        {problems.length > 0 && <div role="alert" className="bid-summary"><strong>{problems.length === 1 ? "1 thing needs fixing" : `${problems.length} things need fixing`}. Your entries are kept.</strong><ul>{problems.map(([key, text]) => <li key={key}>{text}</li>)}</ul></div>}
        {message && <p role="alert" className="field-error">{message}</p>}
        {!keying && <div className="bid-readonly">
          {bid && <dl className="vendor-facts">
            <dt>Status</dt><dd>{bid.late ? "Late, Accepted By Override" : bid.status === "draft" ? "Draft" : "Submitted"}</dd>
            <dt>Vendor Sent It On</dt><dd>{(() => { const w = wallClock(bid.sent_at, zone); return `${w.date} ${w.time} ${zone}`; })()}</dd>
            <dt>Keyed By Us</dt><dd>{formatDateTime(bid.keyed_at)}</dd>
            {bid.late && <><dt>Accepted By</dt><dd>{bid.late.accepted_by_email ? <Person name={bid.late.accepted_by_name} email={bid.late.accepted_by_email} /> : "A lead"}, {formatDateTime(bid.late.at)}: {bid.late.reason}</dd></>}
            {bid.replaced_by_bid_id && <><dt>Replaced By</dt><dd><button type="button" className="bid-link" disabled={busy} onClick={() => void follow(bid.replaced_by_bid_id!)}>Open the replacement bid</button></dd></>}
            {bid.replaces_bid_id && <><dt>Replaces</dt><dd><button type="button" className="bid-link" disabled={busy} onClick={() => void follow(bid.replaces_bid_id!)}>Open the original bid</button></dd></>}
          </dl>}
          {bid && isFull(bid) ? <FullBid bid={bid} rfq={rfq} /> : <p role="status"><CheckCircle aria-hidden="true" weight="regular" /> {bid?.status === "draft" ? "This draft is still being keyed." : "Received. The figures stay sealed until the bidding stop time."}</p>}
          {bid?.status === "submitted" && !bid.replaced_by_bid_id && (passed ? <p>Bids can no longer be replaced after the bidding stop time.</p> : rfq.can_edit && <ReplaceForm rfq={rfq} bid={bid} onReplaced={next => { switchTo(next); onChanged(); }} />)}
          {bid?.status === "draft" && !isFull(bid) && <LateAccept lead={lead} late={isLate(bid, rfq.deadline_at)} reason={reason} setReason={setReason} error={errors.reason} busy={busy} onAccept={() => void submit(true)} />}
        </div>}
        {keying && <form className="bid-form" noValidate onSubmit={e => { e.preventDefault(); void run(save); }}>
          <fieldset className="req-fields"><legend className="t-label">Who and when</legend>
            <div className="req-field"><label className="t-label" htmlFor="bid-version">Version Answered</label>
              <select id="bid-version" value={version} onChange={e => setVersion(e.target.value)} disabled={!!full}>{Array.from({ length: rfq.version }, (_, i) => <option key={i + 1} value={i + 1}>Version {i + 1}</option>)}</select></div>
            <div className="req-field" data-invalid={errors.currency ? "true" : undefined}><label className="t-label" htmlFor="bid-currency">Vendor's Currency</label>
              <input id="bid-currency" value={currency} maxLength={3} onChange={e => setCurrency(e.target.value.toUpperCase())} aria-invalid={!!errors.currency || undefined} /></div>
            <div className="req-field" data-invalid={errors.sent ? "true" : undefined}><label className="t-label" htmlFor="bid-date">Vendor Sent It On (date)</label>
              <input id="bid-date" value={date} onChange={e => setDate(e.target.value)} inputMode="numeric" placeholder="YYYY-MM-DD" aria-invalid={!!errors.sent || undefined} aria-describedby={errors.sent ? "bid-sent-error" : undefined} /></div>
            <div className="req-field" data-invalid={errors.sent ? "true" : undefined}><label className="t-label" htmlFor="bid-time">Time (24-hour, {zone})</label>
              <input id="bid-time" value={time} onChange={e => setTime(e.target.value)} inputMode="numeric" placeholder="HH:MM" aria-invalid={!!errors.sent || undefined} aria-describedby={errors.sent ? "bid-sent-error" : undefined} />
              {errors.sent && <p id="bid-sent-error" className="field-error">{errors.sent}</p>}</div>
            <div className="req-field" data-wide data-invalid={errors.evidence ? "true" : undefined}><label className="t-label" htmlFor="bid-evidence">Evidence (the vendor's email or file)</label>
              <input id="bid-evidence" type="file" onChange={e => setFile(e.target.files?.[0] ?? null)} aria-invalid={!!errors.evidence || undefined} aria-describedby={errors.evidence ? "bid-evidence-error" : undefined} />
              {full?.evidence_attachment_id && !file && <span className="req-sub">Evidence is attached. Choose a file to replace it.</span>}
              {errors.evidence && <p id="bid-evidence-error" className="field-error">{errors.evidence}</p>}</div>
            <p className="req-sub">Keyed By Us: {full ? formatDateTime(full.keyed_at) : "set when you save"}</p>
          </fieldset>
          <table className="vendor-table bid-lines">
            <caption className="visually-hidden">Lines, in {currency}</caption>
            <thead><tr><th scope="col">Line</th><th scope="col">Not Quoted</th><th scope="col">Unit Price ({currency})</th><th scope="col">Tax ({currency})</th><th scope="col">Line Total ({currency})</th></tr></thead>
            <tbody>{rfq.lines.map((l, i) => { const line = lines[i]!, saved = full?.lines.find(x => x.rfq_line_id === l.id); const bad = lineError(i);
              return <tr key={l.id}>
                <th scope="row" data-label="Line">{l.line_no} · {l.description}<span className="req-sub">{saved ? formatQuantity(saved.quantity) : l.quantity} {l.uom_code}</span>{bad && <span className="field-error" id={`bid-line-error-${i}`}>{bad}</span>}</th>
                <td data-label="Not Quoted"><input type="checkbox" aria-label={`Line ${l.line_no} not quoted`} checked={!line.quoted} onChange={e => setLines(set(lines, i, { quoted: !e.target.checked }))} /></td>
                <td data-label="Unit Price">{line.quoted ? <input className="numeric" aria-label={`Unit price, line ${l.line_no} (${currency})`} inputMode="decimal" value={line.price} onChange={e => setLines(set(lines, i, { price: e.target.value }))} aria-invalid={!!bad || undefined} aria-describedby={bad ? `bid-line-error-${i}` : undefined} /> : "—"}</td>
                <td data-label="Tax">{line.quoted ? <input className="numeric" aria-label={`Tax, line ${l.line_no} (${currency})`} inputMode="decimal" value={line.tax} onChange={e => setLines(set(lines, i, { tax: e.target.value }))} aria-invalid={!!bad || undefined} aria-describedby={bad ? `bid-line-error-${i}` : undefined} /> : "—"}</td>
                <td data-label="Line Total" className="numeric">{saved?.line_total ? `${formatMoney(saved.line_total, currency, navigator.language)} ${currency}` : "—"}</td>
              </tr>; })}</tbody>
          </table>
          {showTotals && <Totals totals={showTotals} currency={currency} />}
          <div className="req-fields">
            <div className="req-field" data-invalid={errors.freight ? "true" : undefined}><label className="t-label" htmlFor="bid-freight">Freight ({currency})</label>
              <input id="bid-freight" className="numeric" inputMode="decimal" value={freight} onChange={e => setFreight(e.target.value)} aria-invalid={!!errors.freight || undefined} />{errors.freight && <p className="field-error">{errors.freight}</p>}</div>
            <div className="req-field" data-invalid={errors.lead ? "true" : undefined}><label className="t-label" htmlFor="bid-lead">Lead Time (days)</label>
              <input id="bid-lead" inputMode="numeric" value={leadTime} onChange={e => setLeadTime(e.target.value)} aria-invalid={!!errors.lead || undefined} />{errors.lead && <p className="field-error">{errors.lead}</p>}</div>
            <div className="req-field"><label className="t-label" htmlFor="bid-valid">Valid Until</label><input id="bid-valid" type="date" value={validUntil} onChange={e => setValidUntil(e.target.value)} /></div>
            <div className="req-field"><label className="t-label" htmlFor="bid-terms">Delivery Terms</label><input id="bid-terms" value={terms} maxLength={500} onChange={e => setTerms(e.target.value)} /></div>
            <div className="req-field" data-wide><label className="t-label" htmlFor="bid-warranty">Warranty</label><textarea id="bid-warranty" rows={2} maxLength={2000} value={warranty} onChange={e => setWarranty(e.target.value)} /></div>
            <div className="req-field" data-wide><label className="t-label" htmlFor="bid-exceptions">Exceptions</label><textarea id="bid-exceptions" rows={2} maxLength={5000} value={exceptions} onChange={e => setExceptions(e.target.value)} /></div>
          </div>
          {open.length > 0 && <fieldset className="bid-requirements"><legend className="t-label">Requirements</legend>{open.map((q, i) => { const bad = errors[`requirements/${q.id}`];
            return <div className="req-field" key={q.id} data-invalid={bad ? "true" : undefined}>
              <span id={`bid-q-${i}`}>{q.text}</span>
              <div role="radiogroup" aria-labelledby={`bid-q-${i}`} className="req-actions">
                {[["met", "Met"], ["not", "Not Met"]].map(([value, text]) => <label key={value}><input type="radio" name={`bid-q-${i}`} checked={answers[i]!.met === value} onChange={() => setAnswers(set(answers, i, { met: value! }))} /> {text}</label>)}
              </div>
              <label className="visually-hidden" htmlFor={`bid-note-${i}`}>Note for: {q.text}</label>
              <input id={`bid-note-${i}`} placeholder="Note" maxLength={1000} value={answers[i]!.note} onChange={e => setAnswers(set(answers, i, { note: e.target.value }))} />
              {bad && <p className="field-error">{bad}</p>}</div>; })}</fieldset>}
          {late && <div className="rfq-banner" role="status"><Warning aria-hidden="true" weight="regular" /><span><strong>This bid is late.</strong> {LATE}{" "}
            {lead ? "As a sourcing lead you can accept it with a reason; the override is audited." : "Ask a sourcing lead to open this draft and accept it."} Your draft is saved.</span></div>}
          {late && lead && <div className="req-field" data-invalid={errors.reason ? "true" : undefined}><label className="t-label" htmlFor="bid-late-reason">Reason for Accepting a Late Bid</label>
            <textarea id="bid-late-reason" rows={2} maxLength={500} value={reason} onChange={e => setReason(e.target.value)} aria-invalid={!!errors.reason || undefined} /></div>}
          <div className="req-actions">
            <button type="submit" disabled={busy}>Save Draft</button>
            {late && lead ? <button type="button" disabled={busy} onClick={() => void submit(true)}>Accept Late Bid and Save</button>
              : <button type="button" disabled={busy} onClick={() => void submit(false)}>Submit Bid</button>}
          </div>
        </form>}
      </Dialog.Content>
    </Dialog.Portal>
  </Dialog.Root>;
}

function Totals({ totals, currency }: { totals: NonNullable<Bid["totals"]>; currency: string }) {
  return <table className="vendor-table bid-totals"><caption className="visually-hidden">Totals, in {currency}</caption>
    <tbody>{([["Lines", totals.lines], ["Tax", totals.tax], ["Freight", totals.freight], ["Total", totals.total]] as const).map(([name, value]) =>
      <tr key={name}><th scope="row">{name}</th><td className="numeric">{formatMoney(value, currency, navigator.language)} {currency}</td></tr>)}</tbody></table>;
}

function FullBid({ bid, rfq }: { bid: Bid; rfq: Rfq }) {
  const none = "Not given";
  return <>
    <dl className="vendor-facts">
      <dt>Currency</dt><dd>{bid.currency}</dd>
      <dt>Version Answered</dt><dd>Version {bid.rfq_version}</dd>
      <dt>Evidence</dt><dd>{bid.evidence_attachment_id ? <a href={`/api/v1/attachments/${bid.evidence_attachment_id}/content`} target="_blank" rel="noreferrer">Open the vendor's email or file</a> : none}</dd>
      <dt>Freight ({bid.currency})</dt><dd className="numeric">{formatMoney(bid.freight, bid.currency, navigator.language)}</dd>
      <dt>Lead Time</dt><dd>{bid.lead_time_days === null || bid.lead_time_days === undefined ? none : `${bid.lead_time_days} days`}</dd>
      <dt>Valid Until</dt><dd>{bid.valid_until ? formatDate(bid.valid_until) : none}</dd>
      <dt>Delivery Terms</dt><dd>{bid.delivery_terms || none}</dd>
      <dt>Warranty</dt><dd>{bid.warranty || none}</dd>
      <dt>Exceptions</dt><dd>{bid.exceptions || none}</dd>
    </dl>
    <table className="vendor-table"><caption className="visually-hidden">Lines, in {bid.currency}</caption>
      <thead><tr><th scope="col">Line</th><th scope="col">Unit Price ({bid.currency})</th><th scope="col">Tax ({bid.currency})</th><th scope="col">Line Total ({bid.currency})</th></tr></thead>
      <tbody>{bid.lines.map(l => { const r = rfq.lines.find(x => x.id === l.rfq_line_id); return <tr key={l.rfq_line_id}>
        <th scope="row">{r?.line_no} · {r?.description}</th>
        {l.quoted ? <><td className="numeric">{formatQuantity(l.unit_price ?? "0")}</td><td className="numeric">{formatQuantity(l.tax)}</td><td className="numeric">{formatMoney(l.line_total ?? "0", bid.currency, navigator.language)}</td></> : <td colSpan={3}>Not Quoted</td>}</tr>; })}</tbody></table>
    {bid.totals && <Totals totals={bid.totals} currency={bid.currency} />}
    {bid.requirements.length > 0 && <table className="vendor-table"><caption className="visually-hidden">Compliance answers</caption>
      <thead><tr><th scope="col">Requirement</th><th scope="col">Answer</th><th scope="col">Note</th></tr></thead>
      <tbody>{bid.requirements.map(a => <tr key={a.requirement_id}><th scope="row">{rfq.requirements?.find(q => q.id === a.requirement_id)?.text ?? "Requirement"}</th><td>{a.met ? "Met" : "Not Met"}</td><td>{a.note || "—"}</td></tr>)}</tbody></table>}
  </>;
}

function LateAccept({ lead, late, reason, setReason, error, busy, onAccept }: { lead: boolean; late: boolean; reason: string; setReason: (v: string) => void; error?: string; busy: boolean; onAccept: () => void }) {
  if (!late) return <p>This draft was sent on time. The buyer who keyed it submits it.</p>;
  return <>
    <div className="rfq-banner" role="status"><Warning aria-hidden="true" weight="regular" /><span><strong>This bid is late.</strong> {LATE} {lead ? "As a sourcing lead you can accept it with a reason; the override is audited." : "Ask a sourcing lead to open this draft and accept it."}</span></div>
    {lead && <div className="req-field" data-invalid={error ? "true" : undefined}><label className="t-label" htmlFor="bid-late-reason">Reason for Accepting a Late Bid</label>
      <textarea id="bid-late-reason" rows={2} maxLength={500} value={reason} onChange={e => setReason(e.target.value)} aria-invalid={!!error || undefined} />{error && <p className="field-error">{error}</p>}
      <div className="req-actions"><button type="button" disabled={busy} onClick={onAccept}>Accept Late Bid and Save</button></div></div>}
  </>;
}
