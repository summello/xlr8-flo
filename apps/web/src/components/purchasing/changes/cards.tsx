import { Warning } from "@phosphor-icons/react";
import Money from "../../ui/Money";
import type { Po, PoChange } from "../api";
import { approvalLine, type DiffEntry } from "./approval";

type Effect = { project_id: string; department_code: string | null; ledger_account_code: string | null; currency: string; delta: string };

/** `What Changes`: the server's diff, in words, verbatim. The figures are never recomputed here. */
export function DiffCard({ change, po, updating, rejected }: { change: PoChange; po: Po; updating: boolean; rejected: boolean }) {
  const diff = change.diff as DiffEntry[];
  return <section className="req-card" aria-labelledby="chg-diff-h">
    <header className="req-card-head"><h2 id="chg-diff-h">What Changes</h2></header>
    <p role="status" className="req-sub">{updating ? "Updating…" : ""}</p>
    {rejected && <p className="req-sub">Showing the last figures the server accepted.</p>}
    {diff.length === 0 ? <p>No changes yet.</p> : <ul className="vendor-list-plain">{diff.map((d, i) => <li key={i}>
      {d.line_no ? `Line ${d.line_no} · ` : ""}{d.label}:{" "}
      {d.was === null ? "was not set" : <del><span className="visually-hidden">was </span>{d.was}</del>}{" "}
      {d.now === null ? "cleared" : <>now {d.now}</>}</li>)}</ul>}
    <p className="po-total">Order total if applied: <Money zero="value" value={change.total} currency={po.currency} /></p>
  </section>;
}

export function EffectCard({ change, name }: { change: PoChange; name: (projectId: string) => string }) {
  const effects = change.budget_effect as Effect[];
  return <section className="req-card" aria-labelledby="chg-effect-h">
    <header className="req-card-head"><h2 id="chg-effect-h">Budget Effect</h2></header>
    {effects.length === 0 ? <p>No budget effect.</p> : <ul className="vendor-list-plain">{effects.map((x, i) => <li key={i}>
      <span>{name(x.project_id)} · {x.department_code ?? "—"} · {x.ledger_account_code ?? "—"}</span>{" "}
      <Money zero="value" value={x.delta} currency={x.currency} />
      {x.delta.startsWith("-") && <p className="req-sub">Lower: this amount is released from the PO's commitment and held again for the requisition, so another PO can order it without new approval.</p>}
    </li>)}</ul>}
  </section>;
}

export function ApprovalLine({ change }: { change: PoChange }) {
  return <p className="req-sub" role="status"><Warning aria-hidden="true" weight="regular" /> {approvalLine({ material: change.material, diff: change.diff as DiffEntry[] })}</p>;
}
