import { CheckCircle, Circle, Prohibit, Target } from "@phosphor-icons/react";
import { useEffect } from "react";
import { apiClient } from "../../../api/client";
import StatusPill from "../../../components/status/StatusPill";
import Card from "../../../components/sourcing/Card";
import InviteesCard, { BidsCard } from "../../../components/sourcing/bids/BidsCard";
import RfqHeaderActions from "../../../components/sourcing/RfqHeaderActions";
import { LIFECYCLE, relativeDeadline, wallClock, type Rfq, type RfqVersion } from "../../../components/sourcing/api";
import Person from "../../../components/ui/Person";
import { failure, formatDateTime, label } from "../../../components/requisitions/api";
import { useLoad } from "../../../components/requisitions/useLoad";
import "../../../styles/projects.css";
import "../../../styles/requisitions.css";
import "../../../styles/vendors.css";
import "../../../styles/sourcing.css";

const show = (value: unknown) => value === null || value === undefined || value === "" ? "none" : typeof value === "object" ? JSON.stringify(value) : String(value);

/** `lines/<id>/quantity` reads as `Line 2 quantity`; other keys as their words. */
function fieldName(key: string, rfq: Rfq): string {
  const [head, id, field] = key.split("/");
  if (head === "lines" && id && field) { const line = rfq.lines.find(l => l.requisition_line_id === id); return `Line ${line?.line_no ?? "?"} ${label(field).toLowerCase()}`; }
  return label(key.replaceAll("/", " "));
}

function Diff({ version, rfq }: { version: RfqVersion; rfq: Rfq }) {
  const entries = Object.entries(version.diff as Record<string, { was: unknown; now: unknown }>);
  if (entries.length === 0) return null;
  return <ul className="vendor-list-plain">{entries.map(([key, change]) => <li key={key}>{fieldName(key, rfq)}: now {show(change.now)}, <span className="visually-hidden">was </span><s className="rfq-was">{show(change.was)}</s></li>)}</ul>;
}

function Lifecycle({ rfq }: { rfq: Rfq }) {
  if (rfq.status === "cancelled") return <p className="rfq-banner" role="status"><Prohibit aria-hidden="true" weight="regular" /><span><strong>Cancelled.</strong> This RFQ is closed to bids. Its history is kept below.</span></p>;
  const current = LIFECYCLE.findIndex(([status]) => status === rfq.status);
  return <ol className="rfq-steps" aria-label="RFQ lifecycle">{LIFECYCLE.map(([status, text], i) => {
    const done = i < current, here = i === current;
    return <li key={status} aria-current={here ? "step" : undefined}>{done ? <CheckCircle aria-hidden="true" weight="regular" /> : here ? <Target aria-hidden="true" weight="regular" /> : <Circle aria-hidden="true" weight="regular" />}{text}<span className="req-sub">{done ? "Done" : here ? "Current" : "Not Yet"}</span></li>;
  })}</ol>;
}

export default function RfqDetail({ id, navigate }: { id: string; navigate: (href: string) => void }) {
  const rfq = useLoad(async () => {
    const { data, error, response } = await apiClient.GET("/api/v1/sourcing/rfqs/{id}", { params: { path: { id } } });
    if (!data) throw new Error(response?.status === 404 ? "This RFQ does not exist or you cannot see it." : failure(error, "Loading the RFQ"));
    return data;
  }, [id]);
  const versions = useLoad(async () => {
    const { data, error } = await apiClient.GET("/api/v1/sourcing/rfqs/{id}/versions", { params: { path: { id } } });
    if (!data) throw new Error(failure(error, "Loading versions"));
    return data;
  }, [id]);
  const history = useLoad(async () => {
    const { data, error } = await apiClient.GET("/api/v1/sourcing/rfqs/{id}/history", { params: { path: { id } } });
    if (!data) throw new Error(failure(error, "Loading history"));
    return data;
  }, [id]);
  useEffect(() => { if (rfq.data) document.title = `${rfq.data.number} ${rfq.data.title}`; }, [rfq.data]);
  const changed = (next: Rfq) => { rfq.set(next); versions.reload(); history.reload(); };

  if (rfq.error) return <section className="req-page" aria-label="RFQ"><div role="alert"><p>{rfq.error}</p><button type="button" onClick={rfq.reload}>Try again</button></div><a href="/sourcing/rfqs" onClick={e => { e.preventDefault(); navigate("/sourcing/rfqs"); }}>Back to RFQs</a></section>;
  if (!rfq.data) return <section className="req-page" aria-label="RFQ"><div className="req-card req-card-skeleton" aria-busy="true"><p className="req-skeleton" role="status" aria-label="Loading RFQ" /></div></section>;
  const r = rfq.data;
  const requisitions = [...new Map(r.lines.map(l => [l.requisition_id, l.requisition_number])).entries()];
  const closes = wallClock(r.deadline_at, r.deadline_tz ?? "UTC");
  return <section className="req-page vendor-profile" aria-label="RFQ detail">
    <header className="vendor-head">
      <div><h2 className="vendor-name">{r.number} · {r.title}</h2><p className="req-sub">Owner <Person name={r.owner_name} email={r.owner_email} /> · Deadline {closes.date} {closes.time} {r.deadline_tz ?? "UTC"} ({relativeDeadline(r.deadline_at)}) · Version {r.version}</p></div>
      <StatusPill docType="rfq" status={r.status} />
      <RfqHeaderActions rfq={r} onChanged={changed} />
    </header>
    <Lifecycle rfq={r} />
    <div className="req-grid-detail">
      <Card title="Request" load={rfq} wide>
        <table className="vendor-table">
          <caption className="visually-hidden">Requested lines</caption>
          <thead><tr><th scope="col">Line</th><th scope="col">Description</th><th scope="col">Quantity</th><th scope="col">Requisition</th></tr></thead>
          <tbody>{r.lines.map(l => <tr key={l.id}>
            <td data-label="Line">{l.line_no}</td>
            <td data-label="Description">{l.description}{l.specification && <span className="req-sub">{l.specification}</span>}</td>
            <td data-label="Quantity">{l.quantity} {l.uom_code}</td>
            <td data-label="Requisition"><a href={`/requisitions/${l.requisition_id}`} onClick={e => { e.preventDefault(); navigate(`/requisitions/${l.requisition_id}`); }}>{l.requisition_number}</a></td>
          </tr>)}</tbody>
        </table>
      </Card>
      <Card title="Versions" load={versions}>
        {(versions.data ?? []).length === 0 ? <p>No version has been published yet.</p> : <ul className="vendor-list-plain">{[...(versions.data ?? [])].reverse().map(v => <li key={v.version}><strong>Version {v.version}</strong><span className="req-sub"><Person name={v.published_by_name} email={v.published_by_email} />, {formatDateTime(v.published_at)}{v.reason ? `: ${v.reason}` : ""}</span><Diff version={v} rfq={r} /></li>)}</ul>}
      </Card>
      <InviteesCard rfq={r} onChanged={() => { rfq.reload(); versions.reload(); history.reload(); }} />
      <BidsCard rfq={r} navigate={navigate} />
      <Card title="History" load={history}>
        {(history.data ?? []).length === 0 ? <p>No events yet.</p> : <ul className="vendor-list-plain">{(history.data ?? []).map((e, n) => <li key={`${e.at}-${n}`}><strong>{label(e.action.replace(/^rfq\./, "").replaceAll(".", " "))}</strong><span className="req-sub">{e.actor_email ? <Person name={e.actor_name} email={e.actor_email} /> : "System"}, {formatDateTime(e.at)}{e.reason ? `: ${e.reason}` : ""}</span></li>)}</ul>}
      </Card>
      <Card title="Linked Records" load={rfq}>
        <ul className="vendor-list-plain">{requisitions.map(([reqId, number]) => <li key={reqId}><a href={`/requisitions/${reqId}`} onClick={e => { e.preventDefault(); navigate(`/requisitions/${reqId}`); }}>{number}</a></li>)}</ul>
      </Card>
    </div>
  </section>;
}
