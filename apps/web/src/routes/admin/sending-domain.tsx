import { useCallback, useEffect, useRef, useState } from "react";
import { apiClient } from "../../api/client";
import { writeHeaders } from "../../features/auth/api";
import { failure, formatDate, type Problem } from "../../components/requisitions/api";
import { useLoad } from "../../components/requisitions/useLoad";
import Signal from "../../components/ui/Signal";
import { dnsSignal, REASONS, type SendingDomain, type Suppression, type SuppressionPage } from "../../components/admin/email/api";
import RemoveSuppressionDialog from "../../components/admin/email/RemoveSuppressionDialog";
import "../../components/admin/email/sending-domain.css";

const DENIED = "You need the organization settings permission to see this page.";
const POLL_MS = 5000, POLL_MAX = 24;

class Denied extends Error {}

const formatStamp = (iso: string | null) => iso ? new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(iso)) : "Never";

function DomainCard({ denied }: { denied: (d: boolean) => void }) {
  const [status, setStatus] = useState<string | null>(null), [busy, setBusy] = useState(false);
  const polls = useRef(0), before = useRef<string | null>(null), waiting = useRef(false);
  const load = useLoad<SendingDomain>(async () => {
    const { data, response, error } = await apiClient.GET("/api/v1/admin/sending-domain");
    if (response.status === 403) { denied(true); throw new Denied(DENIED); }
    if (!data) throw new Error(failure(error as Problem | undefined, "Loading the sending domain"));
    return data;
  }, []);
  const d = load.data;

  const read = useCallback(async () => {
    const { data } = await apiClient.GET("/api/v1/admin/sending-domain");
    if (!data) return;
    load.set(data);
    if (data.check_pending) {
      if (polls.current >= POLL_MAX) { waiting.current = false; setStatus("Still waiting for the check. Refresh to see the result."); }
      return;
    }
    if (waiting.current) {
      waiting.current = false;
      setStatus(data.checked_at !== before.current ? "Checked just now." : "The check could not reach the email provider. The last result stays in force. Try again in a minute.");
    }
  }, [load]);

  const pending = d?.check_pending ?? false;
  useEffect(() => {
    if (!pending || polls.current >= POLL_MAX) return;
    const timer = setTimeout(() => { polls.current += 1; void read(); }, POLL_MS);
    return () => clearTimeout(timer);
  }, [pending, d, read]);

  const check = async () => {
    setBusy(true); setStatus(null);
    try {
      const { data, response, error } = await apiClient.POST("/api/v1/admin/sending-domain/check", { headers: writeHeaders() });
      if (response.status === 429) setStatus("Checked less than a minute ago. Try again in a minute.");
      else if (data) { before.current = d?.checked_at ?? null; waiting.current = true; polls.current = 0; load.set(data); setStatus("Check queued. The result appears here within two minutes."); }
      else setStatus(failure(error as Problem | undefined, "Queuing the check"));
    } catch { setStatus("Queuing the check failed. Check your connection and try again."); }
    finally { setBusy(false); }
  };

  const spf = dnsSignal("SPF", d?.spf ?? null), dkim = dnsSignal("DKIM", d?.dkim ?? null);
  return <section className="sd-card" aria-labelledby="sd-domain">
    <h2 id="sd-domain">Sending Domain</h2>
    {load.loading && !d ? <span className="sd-skeleton" role="status" aria-label="Loading the sending domain" />
      : load.error && !d ? (load.error === DENIED ? <p>{DENIED}</p> : <p role="alert">We could not load the sending domain. Nothing was changed. <button type="button" onClick={load.reload}>Try Again</button></p>)
      : d ? <>
        {d.po_email_allowed
          ? <Signal tone="success" label="Purchase Orders Can Be Emailed" />
          : <Signal tone="danger" label="Purchase Order Email Is Held" why={<span className="sd-muted">SPF and DKIM must both pass before purchase orders are emailed.</span>} />}
        <div className="sd-signals"><Signal tone={spf.tone} label={spf.label} /><Signal tone={dkim.tone} label={dkim.label} /></div>
        {d.detail && <div><p>What to fix</p><ul className="sd-fix">{d.detail.split("; ").map((line, i) => <li key={i}>{line}</li>)}</ul></div>}
        <p className="sd-muted">The platform operator publishes these DNS records.</p>
        <p className="sd-muted">DMARC is published and verified by hand by the platform operator; it is not checked automatically.</p>
        <div className="sd-row">
          <span>Last checked: <time dateTime={d.checked_at ?? undefined}>{formatStamp(d.checked_at)}</time></span>
          <button type="button" onClick={() => void check()} aria-busy={busy || undefined}>Check Now</button>
        </div>
        <p role="status">{status}</p>
      </> : null}
  </section>;
}

function SuppressionCard({ denied }: { denied: (d: boolean) => void }) {
  const [text, setText] = useState(""), [q, setQ] = useState("");
  const [rows, setRows] = useState<Suppression[]>([]), [next, setNext] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null), [more, setMore] = useState(false);
  const [target, setTarget] = useState<{ row: Suppression; opener: HTMLElement | null } | null>(null);
  const search = useRef<HTMLInputElement>(null), appended = useRef<string | null>(null);
  const load = useLoad<SuppressionPage>(async () => {
    const { data, response, error } = await apiClient.GET("/api/v1/admin/email-suppressions", { params: { query: q ? { q } : {} } });
    if (response.status === 403) { denied(true); throw new Denied(DENIED); }
    if (!data) throw new Error(failure(error as Problem | undefined, "Loading suppressed addresses"));
    setRows(data.rows); setNext(data.next_cursor);
    return data;
  }, [q]);

  useEffect(() => { // focus the first appended row's Remove button
    if (appended.current) { document.getElementById(`sd-remove-${appended.current}`)?.focus(); appended.current = null; }
  }, [rows]);

  const loadMore = async () => {
    if (!next) return;
    setMore(true);
    const { data } = await apiClient.GET("/api/v1/admin/email-suppressions", { params: { query: { ...(q ? { q } : {}), cursor: next } } });
    setMore(false);
    if (!data) { setStatus("We could not load more addresses. Nothing was changed. Try again."); return; }
    appended.current = data.rows[0]?.id ?? null;
    setRows(r => [...r, ...data.rows]); setNext(data.next_cursor);
  };

  const removed = (gone: boolean) => {
    if (!target) return;
    const { row } = target;
    setRows(r => r.filter(x => x.id !== row.id)); setTarget(null);
    setStatus(gone ? "That address was already removed." : `Removed ${row.email}. Future emails to it are sent again.`);
    search.current?.focus();
  };

  return <section className="sd-card" aria-labelledby="sd-suppressed">
    <h2 id="sd-suppressed">Suppressed Addresses</h2>
    <form className="sd-row" role="search" onSubmit={e => { e.preventDefault(); setStatus(null); setQ(text.trim()); }}>
      <label htmlFor="sd-search">Search by email</label>
      <input id="sd-search" ref={search} type="search" value={text} onChange={e => setText(e.target.value)} aria-describedby="sd-search-help" maxLength={254} />
      <button type="submit">Search</button>
      <span id="sd-search-help" className="sd-muted">Matches the start of the address.</span>
    </form>
    <p role="status">{status}</p>
    {load.loading && !load.data ? <span className="sd-skeleton" role="status" aria-label="Loading suppressed addresses" />
      : load.error && !load.data ? (load.error === DENIED ? <p>{DENIED}</p> : <p role="alert">We could not load suppressed addresses. Nothing was changed. <button type="button" onClick={load.reload}>Try Again</button></p>)
      : rows.length === 0 ? <p>{q ? `No suppressed address starts with "${q}".` : "No suppressed addresses. Addresses are added when an email hard-bounces or is marked as spam."}</p>
      : <>
        <table className="sd-table"><caption>Addresses that purchase order emails are not sent to</caption>
          <thead><tr><th scope="col">Email</th><th scope="col">Reason</th><th scope="col">Suppressed On</th><th scope="col"><span className="visually-hidden">Actions</span></th></tr></thead>
          <tbody>{rows.map(r => <tr key={r.id}><td>{r.email}</td><td>{REASONS[r.reason]}</td><td>{formatDate(r.created_at)}</td>
            <td><button type="button" id={`sd-remove-${r.id}`} aria-label={`Remove ${r.email} from the suppression list`} onClick={e => setTarget({ row: r, opener: e.currentTarget })}>Remove</button></td></tr>)}</tbody></table>
        {/* ponytail: a plain table with keyset Load More; switch to DataGrid if an organization's list passes a few hundred rows */}
        {next && <div><button type="button" onClick={() => void loadMore()} aria-busy={more || undefined}>Load More</button></div>}
      </>}
    {target && <RemoveSuppressionDialog row={target.row} opener={target.opener} onClose={() => setTarget(null)} onRemoved={removed} />}
  </section>;
}

export default function SendingDomainPage() {
  const [denied, setDenied] = useState(false);
  return <div className="sd">
    <p>Can purchase orders be delivered? SPF and DKIM must pass before a purchase order is emailed.</p>
    {denied ? <p>{DENIED}</p> : <><DomainCard denied={setDenied} /><SuppressionCard denied={setDenied} /></>}
  </div>;
}
