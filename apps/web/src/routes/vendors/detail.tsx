import { XCircle } from "@phosphor-icons/react";
import { useEffect, useState, type ReactNode } from "react";
import { apiClient } from "../../api/client";
import StatusPill from "../../components/status/StatusPill";
import Person from "../../components/ui/Person";
import Signal from "../../components/ui/Signal";
import DocumentSheet from "../../components/vendors/DocumentSheet";
import VendorDialog, { type DialogKind } from "../../components/vendors/dialogs/VendorDialog";
import "../../components/vendors/questionnaire/questionnaire.css";
import TaxBankingCard from "../../components/vendors/TaxBankingCard";
import Eligibility from "../../components/vendors/Eligibility";
import PerformanceCard from "../../components/vendors/performance/PerformanceCard";
import ScorecardSheet from "../../components/vendors/performance/ScorecardSheet";
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
  const performance = useLoad(async () => {
    const { data, error } = await apiClient.GET("/api/v1/vendors/{id}/performance", { params: { path: { id } } });
    if (!data) throw new Error(failure(error, "Loading performance"));
    return data;
  }, [id]);
  // The onboarding record is the requester's answers; a refusal just leaves the fold out.
  const onboarding = useLoad(async () => (await apiClient.GET("/api/v1/vendors/{id}/onboarding", { params: { path: { id } } })).data ?? null, [id]);
  const [dialog, setDialog] = useState<DialogKind | null>(null);
  const [open, setOpen] = useState<{ doc: DocState; opener: HTMLElement | null } | null>(null);
  const [scorecard, setScorecard] = useState<{ opener: HTMLElement | null } | null>(null);
  useEffect(() => { if (vendor.data) document.title = vendor.data.trading_name; }, [vendor.data]);

  if (vendor.error) return <section className="req-page" aria-label="Vendor"><div role="alert"><p>{vendor.error}</p><button type="button" onClick={vendor.reload}>Try again</button></div><a href="/vendors" onClick={e => { e.preventDefault(); navigate("/vendors"); }}>Back to Vendors</a></section>;
  if (!vendor.data) return <section className="req-page" aria-label="Vendor"><div className="req-card req-card-skeleton" aria-busy="true"><p className="req-skeleton" role="status" aria-label="Loading vendor" /></div></section>;
  const v = vendor.data;
  // A pending `create` is the vendor's own approval; the pending_approval notice covers it.
  const PENDING_NOUN: Record<string, string | undefined> = { suspend: "Suspension", reactivate: "Reactivation", availability: "Availability change" };
  const pending = (changes.data ?? []).find(c => c.status === "pending" && PENDING_NOUN[c.kind]);
  const disabledHere = (v.disabled_in ?? []).some(d => d.unit_id === unit.data?.id);
  // Each control renders only when the server lists it (DECISIONS 238); the server has already applied vendor.allow_local_disable to local_disable_units.
  const may = (a: string) => (v.actions ?? []).includes(a);
  const unitOk = !!unit.data && (v.local_disable_units ?? []).includes(unit.data.id);
  const actions: { kind: DialogKind; text: string; destructive?: boolean }[] = [
    ...(may("request_change") && v.status === "active" ? [{ kind: "suspend" as const, text: "Suspend", destructive: true }] : []),
    ...(may("request_change") && v.status === "suspended" ? [{ kind: "reactivate" as const, text: "Reactivate" }] : []),
    ...(may("request_change") && (v.status === "active" || v.status === "suspended") ? [{ kind: "availability" as const, text: "Change Availability" }] : []),
    ...(unitOk && v.availability === "global" && v.status === "active" && !disabledHere ? [{ kind: "disable" as const, text: "Disable Here" }] : []),
    ...(unitOk && v.availability === "global" && disabledHere ? [{ kind: "enable" as const, text: "Enable Here" }] : []),
  ];
  const answered = Object.keys(onboarding.data?.answers ?? {});
  return <section className="req-page vendor-profile" aria-label="Vendor profile">
    <header className="vendor-head">
      <div><h2 className="vendor-name">{v.trading_name}</h2><p className="req-sub">{v.number} · {v.legal_name}</p></div>
      <StatusPill docType="vendor" status={v.status} />
      {eligibility.data && <Eligibility eligibility={eligibility.data} variant="header" unitName={unit.data?.name} />}
      {actions.length > 0 && <div className="vendor-head-actions">{actions.map(a => <button key={a.kind} type="button" className={a.destructive ? "vendor-destructive" : undefined} onClick={() => setDialog(a.kind)}>{a.destructive && <XCircle aria-hidden="true" />}{a.text}</button>)}</div>}
    </header>
    {pending && <p className="req-notice" role="status">{PENDING_NOUN[pending.kind]} requested by {pending.requested_by_me ? "you" : <Person name={pending.requested_by_name} email={pending.requested_by_email} />}, awaiting approval.</p>}
    {v.status === "pending_approval" && <p className="req-notice" role="status">This vendor is waiting for approval. You can read it here; nothing can be changed until it is decided.</p>}
    <div className="req-grid-detail">
      <Card title="Profile" load={vendor}>
        <dl className="vendor-facts"><dt>Created by</dt><dd><Person name={v.created_by_name} email={v.created_by_email} /></dd><dt>Created</dt><dd>{formatDateTime(v.created_at)}</dd><dt>Updated</dt><dd>{formatDateTime(v.updated_at)}</dd></dl>
        {onboarding.data && <details><summary>Onboarding Record</summary>
          <p className="req-sub">Questionnaire version {onboarding.data.version_number}</p>
          {answered.length === 0 ? <p>No answers recorded.</p> : <dl className="vendor-facts">{onboarding.data.questions.map(q => <><dt key={`${q.key}-q`}>{q.prompt}</dt><dd key={`${q.key}-a`}>{String(onboarding.data!.answers[q.key] ?? "—")}</dd></>)}</dl>}
        </details>}
      </Card>
      <Card title="Availability" load={vendor}>
        <p>{AVAILABILITY[v.availability]}</p>
        {(v.units ?? []).length > 0 && <ul>{(v.units ?? []).map(u => <li key={u.unit_id}>{u.unit_code}</li>)}</ul>}
        {(v.disabled_in ?? []).map(d => <p key={d.unit_id} className="req-sub">Disabled in {d.unit_code} by <Person name={d.by_name} email={d.by_email} /> on {formatDate(d.at)}: {d.reason}</p>)}
      </Card>
      <Card title="Contacts" load={contacts}>
        {(contacts.data ?? []).length === 0 ? <p>No contacts yet.</p> : <ul className="vendor-list-plain">{(contacts.data ?? []).map(c => <li key={c.id}><strong>{label(c.type)}</strong> <span><Person name={c.name} email={c.email} /></span>{c.phone && <span className="req-sub">{c.phone}</span>}</li>)}</ul>}
      </Card>
      <Card title="Tax and Banking" load={vendor}>
        <TaxBankingCard key={id} vendorId={id} masked={v.sensitive ?? {}} access={v.sensitive_access ?? { can_reveal: false, ask: "an administrator" }} onSaved={vendor.reload} />
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
      <Card title="Performance" load={performance}>
        {performance.data && <PerformanceCard performance={performance.data} onOpen={opener => setScorecard({ opener })} />}
      </Card>
      <Card title="History" load={changes}>
        {(changes.data ?? []).length === 0 ? <p>No status changes yet.</p> : <ul className="vendor-list-plain">{(changes.data ?? []).map(c => <li key={c.id}><strong>{label(c.kind)}</strong> · {label(c.status)}<span className="req-sub"><Person name={c.requested_by_name} email={c.requested_by_email} />, {formatDateTime(c.requested_at)}{c.reason ? `: ${c.reason}` : ""}</span></li>)}</ul>}
      </Card>
      <Card title="Linked Records" load={vendor}>
        <p>RFQs and purchase orders that use this vendor appear here. They keep the vendor as it was when they were made.</p>
      </Card>
    </div>
    {dialog && <VendorDialog kind={dialog} vendorId={id} unit={unit.data} onClose={() => setDialog(null)} onDone={() => { setDialog(null); vendor.reload(); changes.reload(); eligibility.reload(); }} />}
    {scorecard && <ScorecardSheet vendorId={id} categories={(performance.data?.categories ?? []).map(c => c.category_code)} opener={scorecard.opener} onClose={() => setScorecard(null)} onChanged={performance.reload} />}
    {open && <DocumentSheet vendorId={id} doc={open.doc} opener={open.opener} onClose={() => setOpen(null)} onChanged={docs.reload} />}
  </section>;
}
