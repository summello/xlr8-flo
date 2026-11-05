import { CheckCircle, Circle, Prohibit, Target, Warning } from "@phosphor-icons/react";
import { useState, type FormEvent } from "react";
import { apiClient } from "../../api/client";
import { writeHeaders } from "../../features/auth/api";
import Attachments from "../requisitions/Attachments";
import { failure, formatDate, formatDateTime, label, type Problem } from "../requisitions/api";
import { useLoad } from "../requisitions/useLoad";
import Card from "../sourcing/Card";
import Money from "../ui/Money";
import Person from "../ui/Person";
import Signal from "../ui/Signal";
import { FUTURE_STATES, HOLDS, LIFECYCLE, sentSignal, type Po } from "./api";

async function unwrap<T>(result: Promise<{ data?: T; error?: unknown }>, action: string): Promise<T> {
  const { data, error } = await result;
  if (data === undefined) throw new Error(failure(error as Problem | undefined, action));
  return data;
}

const address = (a: Po["bill_to"]) => ["line1", "line2", "city", "region", "postal_code", "country"].map(k => a[k]).filter(v => typeof v === "string" && v).join(", ") || "Not set";

/** The lifecycle as a connected stepper; the M4 states are drawn as future. Cancelled and Superseded are banners, never steps. */
export function PoStepper({ po }: { po: Po }) {
  if (po.status === "cancelled") return <p className="rfq-banner" role="status"><Prohibit aria-hidden="true" weight="regular" /><span><strong>Cancelled.</strong> This purchase order is closed. Its history is kept below.</span></p>;
  const current = LIFECYCLE.findIndex(([status]) => status === po.status);
  return <>
    {po.status === "superseded" && <p className="rfq-banner" role="status"><Warning aria-hidden="true" weight="regular" /><span><strong>Superseded.</strong> A later revision replaced this one. Open it from the version control.</span></p>}
    <ol className="rfq-steps po-steps" aria-label="Purchase order lifecycle">{LIFECYCLE.map(([status, text], i) => {
      const done = current >= 0 && i < current, here = i === current;
      const future = FUTURE_STATES.includes(status) && !here && !done;
      return <li key={status} aria-current={here ? "step" : undefined}>{done ? <CheckCircle aria-hidden="true" weight="regular" /> : here ? <Target aria-hidden="true" weight="regular" /> : <Circle aria-hidden="true" weight="regular" />}{text}<span className="req-sub">{done ? "Done" : here ? "Current" : future ? "Future" : "Not Yet"}</span></li>;
    })}</ol>
  </>;
}

/** Order and Vendor, side by side: what the order says, and who it goes to. */
export function Swimlanes({ po }: { po: Po }) {
  const chartfields = [...new Set(po.lines.map(l => `${l.department_code ?? "—"} / ${l.ledger_account_code ?? "—"}`))];
  const name = typeof po.vendor_snapshot.trading_name === "string" ? po.vendor_snapshot.trading_name : "Vendor";
  return <div className="po-lanes">
    <section aria-labelledby="po-lane-order"><h3 id="po-lane-order">Order</h3>
      <dl className="project-facts">
        <div><dt>Bill To</dt><dd>{address(po.bill_to)}</dd></div>
        <div><dt>Ship To</dt><dd>{po.ship_to_address_id ? "Address chosen" : "Not set"}</dd></div>
        <div><dt>Terms</dt><dd>{po.terms ?? "—"}</dd></div>
        <div><dt>Needed by</dt><dd>{formatDate(po.needed_by)}</dd></div>
        <div><dt>Chartfields</dt><dd>{chartfields.join("; ") || "—"}</dd></div>
      </dl></section>
    <section aria-labelledby="po-lane-vendor"><h3 id="po-lane-vendor">Vendor</h3>
      <dl className="project-facts">
        <div><dt>Vendor</dt><dd><a href={`/vendors/${po.vendor_id}`}>{name}</a>{po.vendor_status && <span className="req-sub">Status now: {label(po.vendor_status)}</span>}</dd></div>
        <div><dt>Send To</dt><dd>{po.recipients.length === 0 ? "—" : po.recipients.map((r, i) => <span key={i} className="req-sub"><Person name={String(r.name)} email={String(r.email)} /></span>)}</dd></div>
      </dl>
      {po.status !== "draft" && po.status !== "approval_pending" && po.status !== "approved" && <p className="req-sub">Vendor details are as they were when this PO was issued.</p>}
      {po.vendor_status === "suspended" && po.status === "issued" && <Signal tone="warning" label="This vendor is suspended. The issued PO is unaffected." />}
    </section>
  </div>;
}

export function LinesCard({ po }: { po: Po }) {
  const pair = (v: string) => <Money zero="value" value={v} currency={po.currency} />;
  return <section className="req-card req-table-card" aria-labelledby="po-lines-title">
    <header className="req-card-head"><h2 id="po-lines-title">Lines</h2><span className="req-count">{po.lines.length} {po.lines.length === 1 ? "line" : "lines"}</span></header>
    <table className="vendor-table po-lines">
      <caption className="visually-hidden">Purchase order lines</caption>
      <thead><tr><th scope="col">Line</th><th scope="col" data-numeric>Quantity</th><th scope="col" data-numeric>Unit price</th><th scope="col" data-numeric>Price</th><th scope="col" data-numeric>Tax</th><th scope="col" data-numeric>Freight</th><th scope="col" data-numeric>Line total</th></tr></thead>
      <tbody>{po.lines.map(l => <tr key={l.line_no}>
        <td data-label="Line"><strong>{l.line_no}. {l.description}</strong><span className="req-sub">{l.uom_code}</span></td>
        <td data-label="Quantity" data-numeric className="numeric">{l.quantity}</td>
        <td data-label="Unit price" data-numeric>{pair(l.unit_price)}</td>
        <td data-label="Price" data-numeric>{pair(l.price)}</td>
        <td data-label="Tax" data-numeric>{pair(l.tax)}</td>
        <td data-label="Freight" data-numeric>{pair(l.freight)}</td>
        <td data-label="Line total" data-numeric>{pair(l.line_total)}</td></tr>)}</tbody>
    </table>
    <dl className="project-facts po-totals">
      <div><dt>Price</dt><dd>{pair(po.price_total)}</dd></div>
      <div><dt>Tax</dt><dd>{pair(po.tax_total)}</dd></div>
      <div><dt>Freight</dt><dd>{pair(po.freight_total)}</dd></div>
      <div><dt><strong>Total</strong></dt><dd><strong>{pair(po.total)}</strong></dd></div>
      {po.currency !== po.converted_currency && <div><dt>Total in {po.converted_currency}</dt><dd><Money zero="value" value={po.total_converted} currency={po.converted_currency} /></dd></div>}
      {po.released_amount && po.released_currency && <div><dt>Released commitment</dt><dd><Money zero="value" value={po.released_amount} currency={po.released_currency} /></dd></div>}
    </dl>
  </section>;
}

export function TransmissionsCard({ id }: { id: string }) {
  const load = useLoad(() => unwrap(apiClient.GET("/api/v1/purchase-orders/{id}/transmissions", { params: { path: { id } } }), "Loading transmissions"), [id]);
  const rows = load.data ?? [];
  const latest = rows.filter(r => r.attempt === Math.max(...rows.map(x => x.attempt)));
  return <Card title="Transmissions" load={load}>
    {rows.length === 0 ? <p>Nothing has been sent to the vendor yet.</p> : <>
      <div><h3>Sent to Vendor</h3><ul className="vendor-list-plain">{latest.map(r => <li key={r.id}><Signal tone={sentSignal(r).tone} label={sentSignal(r).label} /><span className="req-sub"><Person name={r.recipient_name} email={r.recipient_email} /></span></li>)}</ul></div>
      <h3>Send log</h3>
      <ul className="vendor-list-plain">{rows.map(r => <li key={r.id}><strong>Attempt {r.attempt}, revision {r.revision}</strong><span className="req-sub"><Person name={r.recipient_name} email={r.recipient_email} /> · {sentSignal(r).label} · queued {formatDateTime(r.created_at)}</span></li>)}</ul></>}
  </Card>;
}

/** One block per project of the lines. The figures are the server's: nothing here adds or subtracts. */
export function BudgetCard({ id, navigate }: { id: string; navigate: (href: string) => void }) {
  const load = useLoad(async () => {
    const { data, error, response } = await apiClient.GET("/api/v1/purchase-orders/{id}/budget-summary", { params: { path: { id } } });
    if (data) return { kind: "ok", projects: data.projects } as const;
    if (response?.status === 403) return { kind: "forbidden" } as const;
    const problem = error as Problem | undefined;
    if (response?.status === 409 && problem?.checks?.problem === "superseded" && problem.checks.current_id) return { kind: "superseded", currentId: problem.checks.current_id } as const;
    throw new Error(failure(problem, "Loading the budget summary"));
  }, [id]);
  const state = load.data;
  return <Card title="Budget Summary" load={load}>
    {state?.kind === "forbidden" && <p role="status">Budget figures are unavailable: your access does not include this project's balance.</p>}
    {state?.kind === "superseded" && <p role="status">This revision was replaced. Budget figures are on the current revision. <a href={`/purchase-orders/${state.currentId}`} onClick={e => { e.preventDefault(); navigate(`/purchase-orders/${state.currentId}`); }}>Open the current revision</a></p>}
    {state?.kind === "ok" && state.projects.map(p => <div key={p.project_id} className="po-budget"><h3><a href={`/projects/${p.project_id}`}>{p.project_code} {p.project_name}</a></h3>
      <dl className="project-facts">
        <div><dt>Committed</dt><dd><Money zero="value" value={p.committed} currency={p.currency} /></dd></div>
        <div><dt>This PO <span className="req-sub">{label(p.this_po_state)}</span></dt><dd><Money zero="value" value={p.this_po} currency={p.currency} /></dd></div>
        <div><dt>Available After Issue</dt><dd><Money zero="value" value={p.available_after_issue} currency={p.currency} /></dd></div>
        <div><dt>Project Actual</dt><dd><Money zero="value" value={p.actual} currency={p.currency} /></dd></div>
      </dl><p className="req-sub">{HOLDS[p.this_po_state]}</p></div>)}
    {state?.kind === "ok" && state.projects.length === 0 && <p>This purchase order has no lines in a project.</p>}
  </Card>;
}

export function HistoryCard({ id }: { id: string }) {
  const load = useLoad(() => unwrap(apiClient.GET("/api/v1/purchase-orders/{id}/history", { params: { path: { id } } }), "Loading history"), [id]);
  const entries = load.data?.entries ?? [];
  return <Card title="History" load={load}>
    {entries.length === 0 ? <p>Nothing has happened yet.</p> : <ol className="req-history">{entries.map((e, i) => <li key={i}><strong>{label(e.action.replace(/^purchase_order\./, "").replaceAll(".", " "))}</strong>
      <span>{formatDateTime(e.occurred_at)} · {e.actor ? <Person name={null} email={e.actor} /> : "System"}</span>{e.detail && <q>{e.detail}</q>}</li>)}</ol>}
    {load.data?.truncated && <p className="req-sub" role="status">Showing the first 200 entries.</p>}
  </Card>;
}

/** The requisition's linked records: the award and the other documents raised against it, as the server lets this viewer see them. */
export function LinkedCard({ po }: { po: Po }) {
  const load = useLoad(() => unwrap(apiClient.GET("/api/v1/requisitions/{id}/links", { params: { path: { id: po.requisition_id } } }), "Loading linked records"), [po.requisition_id]);
  const nodes = (load.data?.nodes ?? []).filter(n => n.id !== po.id);
  return <Card title="Linked Records" load={load}>
    <ul className="req-linked">
      <li><a href={`/requisitions/${po.requisition_id}`}>Requisition</a><span>Requisition</span></li>
      {nodes.map(n => <li key={n.key}><a href={n.href}>{n.label}</a><span>{label(n.kind)}</span><span>{n.state}</span></li>)}
    </ul>
    {load.data?.partial && <p role="status">Some records are hidden because you do not have access to them.</p>}
  </Card>;
}

export function NotesAndFiles({ id }: { id: string }) {
  const load = useLoad(() => unwrap(apiClient.GET("/api/v1/documents/{document_type}/{document_id}/notes", { params: { path: { document_type: "purchase_order", document_id: id } } }), "Loading notes"), [id]);
  const [body, setBody] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const add = async (e: FormEvent) => {
    e.preventDefault();
    if (!body.trim()) { setProblem("Write a note before adding it."); return; }
    setBusy(true); setProblem(null);
    const { error, response } = await apiClient.POST("/api/v1/documents/{document_type}/{document_id}/notes", { params: { path: { document_type: "purchase_order", document_id: id } }, headers: writeHeaders(), body: { body, visibility: "internal" } });
    setBusy(false);
    if (!response.ok) { setProblem(failure(error as Problem | undefined, "Adding the note")); return; }
    setBody(""); load.reload();
  };
  return <>
    <Card title="Notes and Files" load={load}>
      {(load.data ?? []).length === 0 ? <p>No notes yet. A note here stays internal; it is never sent to the vendor.</p> : <ul className="vendor-list-plain">{(load.data ?? []).map(n => <li key={n.id}><span>{n.body}</span><span className="req-sub"><Person name={n.author_name} email={n.author_email} />, {formatDateTime(n.created_at)} · Internal</span></li>)}</ul>}
      <form onSubmit={e => void add(e)} className="po-note">
        <label htmlFor="po-note-body">Add a note</label>
        <textarea id="po-note-body" rows={2} value={body} onChange={e => setBody(e.target.value)} aria-invalid={problem ? true : undefined} aria-describedby={problem ? "po-note-error" : undefined} />
        {problem && <p id="po-note-error" role="alert" className="field-error">{problem}</p>}
        <button type="submit" aria-busy={busy || undefined}>Add note</button>
      </form>
    </Card>
    <Attachments id={id} documentType="purchase_order" />
  </>;
}
