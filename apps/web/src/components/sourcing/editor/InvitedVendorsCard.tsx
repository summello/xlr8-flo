import { MagnifyingGlass, Trash } from "@phosphor-icons/react";
import { useState } from "react";
import { apiClient } from "../../../api/client";
import Eligibility from "../../vendors/Eligibility";
import { failure, mutationHeaders, type Problem } from "../../requisitions/api";
import { useLoad } from "../../requisitions/useLoad";
import Person from "../../ui/Person";
import { MAX_INVITEES, type Invitee, type Rfq } from "../api";
import type { CardProps } from "./cards";
import { write, type Candidate } from "./api";

const nameOf = (i: Invitee) => String(i.vendor_snapshot.trading_name ?? "Vendor");

/** One invitee: their contacts are editable on a draft and on an amendment (saved per vendor, outside the amend submit); removal is draft only. */
function InviteeRow({ rfq, invitee, mode, onRfq, report }: { rfq: Rfq; invitee: Invitee; mode: CardProps["mode"]; onRfq: (next: Rfq) => void; report: (message: string | null) => void }) {
  const [picked, setPicked] = useState<string[]>(invitee.contact_ids);
  const [busy, setBusy] = useState(false);
  const name = nameOf(invitee);
  const act = async (run: Parameters<typeof write>[0], action: string) => {
    setBusy(true); report(null);
    const done = await write(run, rfq, action);
    setBusy(false);
    if (done.rfq) onRfq(done.rfq); else report(done.message);
  };
  return <li className="rfq-invitee">
    <strong>{name}</strong>
    <span className="req-sub">{invitee.eligible === false ? `Not eligible: ${invitee.reasons.join("; ")}` : invitee.eligible ? "Eligible" : "Eligibility unknown"}{invitee.override_reason ? ` · Overridden: ${invitee.override_reason}` : ""}</span>
    <fieldset className="perf-field"><legend>Send {name} the RFQ at</legend>
      {(invitee.contacts ?? []).map(c => <label key={c.id}><input type="checkbox" checked={picked.includes(c.id)} onChange={e => setPicked(list => e.target.checked ? [...list, c.id] : list.filter(id => id !== c.id))} /> <Person name={c.name} email={c.email} /></label>)}
    </fieldset>
    <div className="req-actions">
      <button type="button" aria-busy={busy || undefined} aria-label={`Save contacts for ${name}`} onClick={() => picked.length === 0 ? report(`Choose at least one contact to send ${name} the RFQ.`) : void act(h => apiClient.PATCH("/api/v1/sourcing/rfqs/{id}/invitees/{vendor_id}", { params: { path: { id: rfq.id, vendor_id: invitee.vendor_id } }, headers: h, body: { contact_ids: picked } }), "Saving the contacts")}>Save Contacts</button>
      {mode === "edit" && <button type="button" aria-label={`Remove ${name}`} onClick={() => void act(h => apiClient.DELETE("/api/v1/sourcing/rfqs/{id}/invitees/{vendor_id}", { params: { path: { id: rfq.id, vendor_id: invitee.vendor_id } }, headers: h }), "Removing the vendor")}><Trash aria-hidden="true" weight="regular" />Remove</button>}
    </div>
  </li>;
}

type Pick = { override: string | null };

/** Checkbox rows from the RFQ's own candidates route; it never calls `/vendors`. Not eligible: reasons shown, and `Override` only for `rfq.approve`. */
function Picker({ rfq, q, onRfq, report }: { rfq: Rfq; q: string; onRfq: (next: Rfq) => void; report: (message: string | null) => void }) {
  const [more, setMore] = useState<Candidate[]>([]);
  const [next, setNext] = useState<string | null | undefined>(undefined);
  const [picks, setPicks] = useState<Record<string, Pick>>({});
  const [busy, setBusy] = useState(false);
  const first = useLoad(async () => {
    const { data, error } = await apiClient.GET("/api/v1/sourcing/rfqs/{id}/invitee-candidates", { params: { path: { id: rfq.id }, query: { q, limit: 20 } } });
    if (!data) throw new Error(failure(error, "Loading vendors"));
    return data;
  }, [rfq.id, q]);
  const rows = [...(first.data?.rows ?? []), ...more];
  const cursor = next === undefined ? first.data?.next_cursor ?? null : next;
  const invited = new Set((rfq.invitees ?? []).map(i => i.vendor_id));
  const loadMore = async () => {
    if (!cursor) return;
    const { data, error } = await apiClient.GET("/api/v1/sourcing/rfqs/{id}/invitee-candidates", { params: { path: { id: rfq.id }, query: { q, limit: 20, cursor } } });
    if (data) { setMore(m => [...m, ...data.rows]); setNext(data.next_cursor ?? null); } else report(failure(error, "Loading more vendors"));
  };
  const toggle = (id: string, on: boolean, override: string | null = null) => setPicks(p => ({ ...Object.fromEntries(Object.entries(p).filter(([k]) => k !== id)), ...(on ? { [id]: { override } } : {}) }));

  const invite = async () => {
    const chosen = rows.filter(r => r.vendor_id in picks);
    if (chosen.length === 0) { report("Choose a vendor to invite."); return; }
    const blank = chosen.find(r => !r.eligibility.eligible && !picks[r.vendor_id]!.override?.trim());
    if (blank) { report(`Enter a reason to override ${blank.trading_name}.`); return; }
    setBusy(true); report(null);
    let current = rfq;
    for (const row of chosen) {
      try {
        const { data, error } = await apiClient.POST("/api/v1/sourcing/rfqs/{id}/invitees", { params: { path: { id: rfq.id } }, headers: mutationHeaders(crypto.randomUUID()), body: { vendor_id: row.vendor_id, ...(row.eligibility.eligible ? {} : { override_reason: picks[row.vendor_id]!.override!.trim() }) } });
        if (!data) { report((error as Problem | undefined)?.detail ?? failure(error as Problem | undefined, `Inviting ${row.trading_name}`)); break; }
        current = data; onRfq(data); toggle(row.vendor_id, false);
      } catch { report(failure(undefined, `Inviting ${row.trading_name}`)); break; }
    }
    setBusy(false);
    if (current !== rfq) first.reload();
  };

  if (first.error) return <div role="alert"><p>{first.error}</p><button type="button" onClick={first.reload}>Try again</button></div>;
  if (!first.data) return <p className="req-skeleton req-skeleton-short" role="status" aria-label="Loading vendors" />;
  return <>
    {rows.length === 0 ? <p>No vendors match.</p> : <ul className="vendor-list-plain" aria-label="Vendors you can invite">{rows.map(r => {
      const gone = invited.has(r.vendor_id) || r.invited, mine = picks[r.vendor_id], ok = r.eligibility.eligible;
      return <li key={r.vendor_id} className="rfq-pick">
        <input type="checkbox" id={`pick-${r.vendor_id}`} aria-label={`Invite ${r.trading_name}`} checked={!!mine} disabled={gone || (!ok && !mine)} onChange={e => toggle(r.vendor_id, e.target.checked)} />
        <span><label htmlFor={`pick-${r.vendor_id}`}>{r.trading_name}</label>
          {gone ? <span className="req-sub">Already invited</span> : <Eligibility eligibility={r.eligibility} variant="picker" />}
          {!ok && !gone && rfq.can_approve && !mine && <button type="button" aria-label={`Override eligibility for ${r.trading_name}`} onClick={() => toggle(r.vendor_id, true, "")}>Override</button>}
          {!ok && mine && <span className="form-field"><label htmlFor={`override-${r.vendor_id}`}>Override reason for {r.trading_name}</label>
            <input id={`override-${r.vendor_id}`} value={mine.override ?? ""} onChange={e => toggle(r.vendor_id, true, e.target.value)} /></span>}
        </span>
      </li>;
    })}</ul>}
    {cursor && <button type="button" onClick={() => void loadMore()}>Load More Vendors</button>}
    <div className="req-actions"><button type="button" aria-busy={busy || undefined} onClick={() => void invite()}>Invite Selected</button></div>
  </>;
}

export default function InvitedVendorsCard({ rfq, mode, onRfq, report, errors }: CardProps) {
  const [text, setText] = useState(""), [q, setQ] = useState("");
  const invitees = rfq.invitees ?? [];
  return <section className="req-card req-wide" aria-labelledby="rfq-card-invitees">
    <header className="req-card-head"><h2 id="rfq-card-invitees" tabIndex={-1}>Invited Vendors</h2></header>
    <p id="rfq-invitees" tabIndex={-1} className="req-sub">{invitees.length} of {MAX_INVITEES} invited. {mode === "amend" ? "The vendor list cannot change after publication; contacts can." : ""}</p>
    {errors["rfq-invitees"] && <p role="alert" className="field-error">{errors["rfq-invitees"]}</p>}
    {invitees.length === 0 ? <p>No vendors are invited yet.</p> : <ul className="vendor-list-plain">{invitees.map(i => <InviteeRow key={`${i.vendor_id}-${rfq.edit_revision}`} rfq={rfq} invitee={i} mode={mode} onRfq={onRfq} report={report} />)}</ul>}
    {mode === "edit" && <>
      <h3>Invite Vendors</h3>
      <div className="rfq-filters"><div className="form-field"><label htmlFor="rfq-vendor-search">Search Vendors</label>
        <input id="rfq-vendor-search" autoComplete="off" value={text} onChange={e => setText(e.target.value)} onKeyDown={e => { if (e.key === "Enter") { e.preventDefault(); setQ(text.trim()); } }} /></div>
        <button type="button" onClick={() => setQ(text.trim())}><MagnifyingGlass aria-hidden="true" weight="regular" />Search</button></div>
      <Picker key={q} rfq={rfq} q={q} onRfq={onRfq} report={report} />
    </>}
  </section>;
}
