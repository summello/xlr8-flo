import { useEffect, useState, type ReactNode } from "react";
import { apiClient } from "../../api/client";
import StatusPill from "../../components/status/StatusPill";
import Signal from "../../components/ui/Signal";
import DocumentSheet from "../../components/vendors/DocumentSheet";
import Eligibility from "../../components/vendors/Eligibility";
import { AVAILABILITY, docWords, type DocState } from "../../components/vendors/api";
import { failure, formatDate, formatDateTime, label } from "../../components/requisitions/api";
import { useLoad, type Loaded } from "../../components/requisitions/useLoad";
import "../../styles/projects.css";
import "../../styles/requisitions.css";
import "../../styles/vendors.css";

/** One card with its own loading and error state, so a failing card never blanks the rest (UX-004). */
function Card({ title, load, children, wide }: { title: string; load: Pick<Loaded<unknown>, "error" | "loading" | "data" | "reload">; children: ReactNode; wide?: boolean }) {
  const id = `vendor-card-${title.replaceAll(" ", "-").toLowerCase()}`;
  return <section className={`req-card${wide ? " req-wide" : ""}`} aria-labelledby={id} aria-busy={load.loading || undefined}>
    <header className="req-card-head"><h2 id={id}>{title}</h2></header>
    {load.error ? <div role="alert"><p>{load.error}</p><button type="button" onClick={load.reload}>Try again</button></div>
      : load.loading && load.data === null ? <p className="req-skeleton req-skeleton-short" role="status" aria-label={`Loading ${title}`} /> : children}
  </section>;
}

export default function VendorDetail({ id, navigate }: { id: string; navigate: (href: string) => void }) {
  const vendor = useLoad(async () => {
    const { data, error, response } = await apiClient.GET("/api/v1/vendors/{id}", { params: { path: { id } } });
    if (!data) throw new Error(response?.status === 404 ? "This vendor does not exist or you cannot see it." : failure(error, "Loading the vendor"));
    return data;
  }, [id]);
  const unit = useLoad(async () => (await apiClient.GET("/api/v1/org/units", { params: { query: { page_size: 1 } } })).data?.rows[0] ?? null, []);
  const eligibility = useLoad(async () => {
    if (!unit.data) return null;
    return (await apiClient.GET("/api/v1/vendors/{id}/eligibility", { params: { path: { id }, query: { unit_id: unit.data.id } } })).data ?? null;
  }, [id, unit.data?.id]);
  const contacts = useLoad(async () => {
    const { data, error } = await apiClient.GET("/api/v1/vendors/{id}/contacts", { params: { path: { id } } });
    if (!data) throw new Error(failure(error, "Loading contacts"));
    return data;
  }, [id]);
  const docs = useLoad(async () => {
    const { data, error } = await apiClient.GET("/api/v1/vendors/{id}/documents", { params: { path: { id } } });
    if (!data) throw new Error(failure(error, "Loading documents"));
    return data;
  }, [id]);
  const changes = useLoad(async () => {
    const { data, error } = await apiClient.GET("/api/v1/vendors/{id}/changes", { params: { path: { id } } });
    if (!data) throw new Error(failure(error, "Loading history"));
    return data;
  }, [id]);
  // The onboarding record is the requester's answers; a refusal just leaves the fold out.
  const onboarding = useLoad(async () => (await apiClient.GET("/api/v1/vendors/{id}/onboarding", { params: { path: { id } } })).data ?? null, [id]);
  const [open, setOpen] = useState<{ doc: DocState; opener: HTMLElement | null } | null>(null);
  useEffect(() => { if (vendor.data) document.title = vendor.data.trading_name; }, [vendor.data]);

  if (vendor.error) return <section className="req-page" aria-label="Vendor"><div role="alert"><p>{vendor.error}</p><button type="button" onClick={vendor.reload}>Try again</button></div><a href="/vendors" onClick={e => { e.preventDefault(); navigate("/vendors"); }}>Back to Vendors</a></section>;
  if (!vendor.data) return <section className="req-page" aria-label="Vendor"><div className="req-card req-card-skeleton" aria-busy="true"><p className="req-skeleton" role="status" aria-label="Loading vendor" /></div></section>;
  const v = vendor.data;
  const answered = Object.keys(onboarding.data?.answers ?? {});
  return <section className="req-page vendor-profile" aria-label="Vendor profile">
    <header className="vendor-head">
      <div><h2 className="vendor-name">{v.trading_name}</h2><p className="req-sub">{v.number} · {v.legal_name}</p></div>
      <StatusPill docType="vendor" status={v.status} />
      {eligibility.data && <Eligibility eligibility={eligibility.data} variant="header" unitName={unit.data?.name} />}
    </header>
    {v.status === "pending_approval" && <p className="req-notice" role="status">This vendor is waiting for approval. You can read it here; nothing can be changed until it is decided.</p>}
    <div className="req-grid-detail">
      <Card title="Profile" load={vendor}>
        <dl className="vendor-facts"><dt>Created by</dt><dd>{v.created_by_email}</dd><dt>Created</dt><dd>{formatDateTime(v.created_at)}</dd><dt>Updated</dt><dd>{formatDateTime(v.updated_at)}</dd></dl>
        {onboarding.data && <details><summary>Onboarding Record</summary>
          <p className="req-sub">Questionnaire version {onboarding.data.version_number}</p>
          {answered.length === 0 ? <p>No answers recorded.</p> : <dl className="vendor-facts">{onboarding.data.questions.map(q => <><dt key={`${q.key}-q`}>{q.prompt}</dt><dd key={`${q.key}-a`}>{String(onboarding.data!.answers[q.key] ?? "—")}</dd></>)}</dl>}
        </details>}
      </Card>
      <Card title="Availability" load={vendor}>
        <p>{AVAILABILITY[v.availability]}</p>
        {(v.units ?? []).length > 0 && <ul>{(v.units ?? []).map(u => <li key={u.unit_id}>{u.unit_code}</li>)}</ul>}
        {(v.disabled_in ?? []).map(d => <p key={d.unit_id} className="req-sub">Disabled in {d.unit_code} by {d.by_email} on {formatDate(d.at)}: {d.reason}</p>)}
      </Card>
      <Card title="Contacts" load={contacts}>
        {(contacts.data ?? []).length === 0 ? <p>No contacts yet.</p> : <ul className="vendor-list-plain">{(contacts.data ?? []).map(c => <li key={c.id}><strong>{label(c.type)}</strong> <span>{c.name}</span><span className="req-sub">{c.email}{c.phone ? ` · ${c.phone}` : ""}</span></li>)}</ul>}
      </Card>
      <Card title="Documents" load={docs} wide>
        <table className="vendor-table">
          <caption className="visually-hidden">Documents</caption>
          <thead><tr><th scope="col">Type</th><th scope="col">Standing</th><th scope="col">Expires</th><th scope="col">Verification</th><th scope="col"><span className="visually-hidden">Open</span></th></tr></thead>
          <tbody>{(docs.data ?? []).map(d => { const w = docWords(d.state, d.days_left); return <tr key={d.document_id ?? `missing-${d.type_code}`}>
            <td data-label="Type">{d.type_code}{d.required ? <span className="req-sub">Required</span> : null}</td>
            <td data-label="Standing"><Signal tone={w.tone} label={w.text} /></td>
            <td data-label="Expires">{formatDate(d.expiry_date)}</td>
            <td data-label="Verification">{d.verification ? label(d.verification) : "—"}</td>
            <td><button type="button" aria-label={`Open ${d.type_code}`} onClick={e => setOpen({ doc: d, opener: e.currentTarget })}>Open</button></td>
          </tr>; })}</tbody>
        </table>
        {(docs.data ?? []).length === 0 && <p>No documents are required or supplied yet.</p>}
      </Card>
      <Card title="History" load={changes}>
        {(changes.data ?? []).length === 0 ? <p>No status changes yet.</p> : <ul className="vendor-list-plain">{(changes.data ?? []).map(c => <li key={c.id}><strong>{label(c.kind)}</strong> · {label(c.status)}<span className="req-sub">{c.requested_by_email}, {formatDateTime(c.requested_at)}{c.reason ? `: ${c.reason}` : ""}</span></li>)}</ul>}
      </Card>
      <Card title="Linked Records" load={vendor}>
        <p>RFQs and purchase orders that use this vendor appear here. They keep the vendor as it was when they were made.</p>
      </Card>
    </div>
    {open && <DocumentSheet vendorId={id} doc={open.doc} opener={open.opener} onClose={() => setOpen(null)} onChanged={docs.reload} />}
  </section>;
}
