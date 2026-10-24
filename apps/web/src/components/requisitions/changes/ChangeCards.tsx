import { ArrowDown, ArrowUp, ArrowsLeftRight, Equals, MinusCircle, PencilSimple, PlusCircle, WarningCircle } from "@phosphor-icons/react";
import { useEffect, useRef } from "react";
import Money from "../../ui/Money";
import Person from "../../ui/Person";
import { formatDate, formatDateTime, label } from "../api";
import { qty } from "../model";
import { CHANGE_LABEL, actorOf, consequence, deltaText, outcomeLabel, type ChangeRow, type ChangeSet, type Outcome, type Revision } from "./model";

const CHANGE_ICON = { changed: PencilSimple, added: PlusCircle, removed: MinusCircle } as const;
const CHANGE_TONE = { changed: "info", added: "success", removed: "danger" } as const;
const OUTCOME_ICON: Record<string, typeof Equals> = { unchanged: Equals, released_once: ArrowDown, reserved_more: ArrowUp, moved: ArrowsLeftRight };

/** Rendered by the server's `value_type`; nothing about the type is inferred from other columns. */
function Value({ row, value, currency }: { row: ChangeRow; value: string | null; currency: string }) {
  if (value === null) return <span aria-label="No value">—</span>;
  switch (row.value_type) {
    case "money": return <Money zero="value" value={value} currency={currency} />;
    case "quantity": return <span className="numeric">{qty(value)}</span>;
    case "date": return <>{formatDate(value)}</>;
    default: return <>{value}</>;
  }
}

/** Spec section 4: `Field | Version N | Version N+1 | Change`; old value struck through, new value bold, the change a pill with icon and word. */
export function WhatChanged({ rows, currency, was, now }: { rows: readonly ChangeRow[]; currency: string; was: number; now: number }) {
  return <section className="req-card" aria-labelledby="what-changed-title">
    <header className="req-card-head"><h2 id="what-changed-title">What Changed</h2><span className="req-count" role="status">{rows.length} {rows.length === 1 ? "change" : "changes"}</span></header>
    <table className="diff req-diff">
      <thead><tr><th scope="col">Field</th><th scope="col">Version {was}</th><th scope="col">Version {now}</th><th scope="col">Change</th></tr></thead>
      <tbody>{rows.map(row => {
        const Icon = CHANGE_ICON[row.change];
        const delta = deltaText(row, currency);
        return <tr key={row.field}>
          <th scope="row" data-label="Field">{row.label}</th>
          <td data-label={`Version ${was}`}><del><Value row={row} value={row.was} currency={currency} /></del></td>
          <td data-label={`Version ${now}`}><strong><Value row={row} value={row.now} currency={currency} /></strong></td>
          <td data-label="Change"><span className="req-pill" data-tone={CHANGE_TONE[row.change]}><Icon aria-hidden="true" weight="regular" />{CHANGE_LABEL[row.change]}</span>{delta && <span className="req-sub">{delta}</span>}</td>
        </tr>;
      })}</tbody>
    </table>
  </section>;
}

export function WhatHappens({ set, verb }: { set: ChangeSet; verb: string }) {
  const { heading, text } = consequence(set);
  return <section className="req-card" aria-labelledby="what-happens-title">
    <header className="req-card-head"><h2 id="what-happens-title">What Happens When You {verb}</h2></header>
    <p className="req-pill" data-tone={set.material ? "warning" : "success"}>{heading}</p>
    <p>{text}</p>
  </section>;
}

/** Server figures only. `project_available_after` null is the partial state: this card says so while the diff and the submit still work. */
export function FundingEffect({ set, retry }: { set: ChangeSet; retry: () => void }) {
  const f = set.funding;
  if (!f) return null;
  const Icon = OUTCOME_ICON[f.outcome] ?? Equals;
  return <section className="req-card" id="funding-check" tabIndex={-1} aria-labelledby="funding-effect-title">
    <header className="req-card-head"><h2 id="funding-effect-title">Funding Check</h2></header>
    <dl className="req-funds">
      <div><dt>Reserved Now</dt><dd><Money zero="value" value={f.reserved_now} currency={f.currency} /></dd></div>
      <div><dt>Reserved After</dt><dd><Money zero="value" value={f.reserved_after} currency={f.currency} /></dd></div>
      <div><dt>Project Available After</dt><dd>{f.project_available_after === null ? <span>Not available</span> : <Money zero="value" value={f.project_available_after} currency={f.currency} />}</dd></div>
    </dl>
    <p className="req-pill" data-tone={f.outcome === "reserved_more" ? "warning" : f.outcome === "unchanged" ? "neutral" : "info"}><Icon aria-hidden="true" weight="regular" />{outcomeLabel(f.outcome)}</p>
    {f.note && <p>{f.note}</p>}
    {f.project_available_after === null && <div role="status"><p>The project's available amount could not be checked. The changes and the submit still work; funds are checked under the lock when you submit.</p><button type="button" onClick={retry}>Try again</button></div>}
    <p className="field-help">An increase is checked when you submit, under the lock on the funding row.</p>
  </section>;
}

function Actor({ id, requester }: { id: string; requester: { id: string; email: string; name: string | null } }) {
  const a = actorOf(id, requester);
  return typeof a === "string" ? <>{a}</> : <Person name={a.name} email={a.email} />;
}

export function Versions({ revisions, requester }: { revisions: readonly Revision[]; requester: { id: string; email: string; name: string | null } }) {
  const sorted = [...revisions].sort((a, b) => b.revision_no - a.revision_no);
  return <section className="req-card" aria-labelledby="versions-title">
    <header className="req-card-head"><h2 id="versions-title">Versions</h2></header>
    {sorted.length === 0 ? <p>No versions yet.</p> : <ol className="req-history">{sorted.map(r => <li key={r.revision_no}>
      <strong>Version {r.revision_no} · {label(r.kind)} · {label(r.status)}</strong>
      <span><Actor id={r.created_by} requester={requester} /> · {formatDateTime(r.created_at)}{r.note ? <>: <q>{r.note}</q></> : ""}</span>
    </li>)}</ol>}
  </section>;
}

/** A failed submit: `role=alert` that takes focus. Problems link to the field on the editing screen. */
export function ResultAlert({ outcome, editHref, go, reload }: { outcome: Outcome; editHref: string; go: (href: string) => void; reload: () => void }) {
  const ref = useRef<HTMLElement>(null);
  useEffect(() => { ref.current?.focus(); }, [outcome]);
  return <section ref={ref} className="error-summary req-summary" role="alert" tabIndex={-1} aria-labelledby="result-title">
    <h2 id="result-title"><WarningCircle aria-hidden="true" weight="regular" />{outcome.kind === "submission_invalid" ? `Fix ${outcome.problems.length} ${outcome.problems.length === 1 ? "thing" : "things"} first` : "Not sent"}</h2>
    <p>{outcome.message}</p>
    {outcome.kind === "submission_invalid" && <ul>{outcome.problems.map((p, i) => <li key={`${p.target}-${i}`}>
      <a href={`${editHref}#${p.target}`} onClick={e => { e.preventDefault(); go(`${editHref}#${p.target}`); }}>{p.category}: {p.message}</a></li>)}</ul>}
    {outcome.kind === "stale_version" && <button type="button" onClick={reload}>Reload</button>}
  </section>;
}
