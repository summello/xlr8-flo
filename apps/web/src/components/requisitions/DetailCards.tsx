import { CheckCircle, Circle, Info, Warning, XCircle } from "@phosphor-icons/react";
import Money from "./ReqMoney";
import { qty } from "./model";
import { formatDate, formatDateTime, label, type Decision, type Linked, type Requisition, type RequisitionLine, type StatusEvent } from "./api";

const TONE_ICON = { neutral: Circle, info: Info, success: CheckCircle, warning: Warning, danger: XCircle } as const;

/** A card at its final height with a placeholder body: initial loading (UX-004), never a lone paragraph. */
export function CardSkeleton({ name, wide }: { name: string; wide?: boolean }) {
  return <section className={`req-card req-card-skeleton${wide ? " req-wide" : ""}`} aria-busy="true" aria-label={`Loading ${name}`}>
    <p className="req-skeleton" role="status" aria-label={`Loading ${name}`} />
  </section>;
}

/** Initial load shows the skeleton; a refresh keeps the data on screen and says so (incremental). */
export function CardState({ error, loading, hasData, name, retry, children }: { error: string | null; loading: boolean; hasData: boolean; name: string; retry: () => void; children: React.ReactNode }) {
  if (error) return <div role="alert" className="req-card"><p><strong>Could not load {name}</strong></p><p>{error}</p><button type="button" onClick={retry}>Try again</button></div>;
  if (loading && !hasData) return <CardSkeleton name={name} />;
  return <div aria-busy={loading || undefined} className="req-refresh">{children}{loading && <p role="status" className="req-refreshing">Refreshing {name}…</p>}</div>;
}

export function RequestCard({ row, delivery }: { row: Requisition; delivery: string | null }) {
  const facts: [string, React.ReactNode][] = [
    ["Justification", row.justification ?? "—"],
    ["Project", row.project_id ? <a href={`/projects/${row.project_id}`}>{row.project_number} {row.project_name}</a> : "Project Request"],
    ["Business unit", row.bu_name], ["Department", row.department_code ?? "—"], ["Ledger account", row.ledger_account_code ?? "—"],
    ["Needed by", formatDate(row.needed_by)], ["Delivery", delivery ?? "—"], ["Estimate", <Money key="e" value={row.estimate} currency={row.currency} />],
  ];
  return <section className="req-card req-wide" aria-labelledby="request-title">
    <header className="req-card-head"><h2 id="request-title">Request</h2></header>
    <dl className="project-facts">{facts.map(([k, v]) => <div key={k}><dt>{k}</dt><dd>{v}</dd></div>)}</dl>
  </section>;
}

export function ReservationCard({ row }: { row: Requisition }) {
  const r = row.reservation;
  return <section className="req-card" aria-labelledby="reservation-title">
    <header className="req-card-head"><h2 id="reservation-title">Reservation</h2></header>
    {row.standalone && !r ? <p>Funding is assigned when a Project Request is approved. Nothing is reserved until then.</p> :
      !r ? <p>{row.status === "draft" ? <>Nothing is reserved yet. Submitting would reserve <Money value={row.estimate} currency={row.currency} /> against {row.project_name ?? "the project"}.</> : "Nothing is reserved."}</p> :
      <dl className="req-funds">
        <div><dt>Reserved</dt><dd className="req-big"><Money value={r.remaining} currency={r.currency} /></dd></div>
        <div><dt>Released</dt><dd><Money value={r.released} currency={r.currency} /></dd></div>
        <div><dt>Project Available</dt><dd>{r.project_available === null ? <span>Not shown<span className="visually-hidden">: you do not hold access to this project's balance</span></span> : <Money value={r.project_available} currency={r.currency} />}</dd></div>
      </dl>}
    {r && r.project_available === null && <p className="field-help">Not shown: your access does not include this project's balance.</p>}
    <p className="field-help">Whatever is not used by the time this closes is released once, and never twice.</p>
  </section>;
}

export function HistoryCard({ events, decisions }: { events: StatusEvent[]; decisions: Decision[] }) {
  const byId = new Map(decisions.map(d => [d.id, d]));
  const used = new Set(events.map(e => e.decision_id).filter(Boolean));
  const entries = [
    ...events.map(e => ({ at: e.at, who: e.system ? "System" : e.actor_email ?? "System",
      what: `${label(e.from_status)} to ${label(e.to_status)}`, note: e.reason ?? (e.decision_id ? byId.get(e.decision_id)?.comment ?? null : null) })),
    ...decisions.filter(d => !used.has(d.id)).map(d => ({ at: d.decided_at, who: d.actor_email, what: `Decision: ${d.outcome}`, note: d.comment })),
  ].sort((a, b) => b.at.localeCompare(a.at));
  return <section className="req-card" aria-labelledby="history-title">
    <header className="req-card-head"><h2 id="history-title">History</h2></header>
    {entries.length === 0 ? <p>Nothing has happened yet.</p> : <ol className="req-history">{entries.map((e, i) => <li key={i}><strong>{e.what}</strong>
      <span>{formatDateTime(e.at)} · {e.who}</span>{e.note && <q>{e.note}</q>}</li>)}</ol>}
  </section>;
}

export function LinkedCard({ nodes, partial }: { nodes: Linked[]; partial: boolean }) {
  return <section className="req-card" aria-labelledby="linked-title">
    <header className="req-card-head"><h2 id="linked-title">Linked Records</h2></header>
    <p className="field-help">Every record raised for this request stays linked to it.</p>
    {partial && <p role="status">Some records are hidden because you do not have access to them.</p>}
    {nodes.length === 0 ? <div className="req-empty"><h3>Nothing Linked Yet</h3></div> :
      <ul className="req-linked">{nodes.map(n => { const Icon = TONE_ICON[n.state_tone]; return <li key={n.key}>
        <a href={n.href}>{n.label}</a><span>{label(n.kind)}</span>
        <span className="req-pill" data-tone={n.state_tone}><Icon aria-hidden="true" weight="regular" />{n.state}</span></li>; })}</ul>}
  </section>;
}

export function LinesCard({ lines }: { lines: RequisitionLine[] }) {
  return <section className="req-card req-wide req-table-card" aria-labelledby="detail-lines-title">
    <header className="req-card-head"><h2 id="detail-lines-title">Lines</h2><span className="req-count">{lines.length} {lines.length === 1 ? "line" : "lines"}</span></header>
    {lines.length === 0 ? <div className="req-empty"><h3>No Lines</h3></div> :
      <table className="req-lines-table req-rollup"><thead><tr><th scope="col">Line</th><th scope="col" data-numeric>Requested</th><th scope="col" data-numeric data-extra>Sourced</th><th scope="col" data-numeric>Awarded</th><th scope="col" data-numeric data-extra>Ordered</th><th scope="col" data-numeric data-extra>Cancelled</th><th scope="col" data-numeric>Remaining</th></tr></thead>
        <tbody>{lines.map(l => <tr key={l.id}>
          <td data-label="Line"><strong>{l.line_no}. {l.description}</strong><span className="req-sub">{label(l.line_type)} · {l.uom_code}</span></td>
          <td data-label="Requested" data-numeric className="numeric">{qty(l.requested_qty)}</td>
          <td data-label="Sourced" data-numeric data-extra className="numeric">{qty(l.sourced_qty)}</td>
          <td data-label="Awarded" data-numeric className="numeric">{qty(l.awarded_qty)}</td>
          <td data-label="Ordered" data-numeric data-extra className="numeric">{qty(l.ordered_qty)}</td>
          <td data-label="Cancelled" data-numeric data-extra className="numeric">{qty(l.cancelled_qty)}</td>
          <td data-label="Remaining" data-numeric className="numeric">{qty(l.remaining_qty)}</td></tr>)}</tbody></table>}
  </section>;
}
