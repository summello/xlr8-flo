import { CheckCircle, Warning } from "@phosphor-icons/react";
import Money from "../ui/Money";
import { formatMoney } from "../../lib/money";
import { scaled } from "../../lib/decimal";
import { fundingFor } from "./model";
import type { SubmissionCheck } from "./api";

/** Advisory only (D-M2-15): "This Requisition" and "Available After" are computed here from the unsaved lines. */
export default function FundingCheck({ check, estimate, currency, standalone, saved, error, retry }: {
  check: SubmissionCheck | null; estimate: string; currency: string; standalone: boolean; saved: boolean; error: string | null; retry: () => void;
}) {
  const route = check?.route;
  let body;
  if (standalone) body = <p>Funding is assigned when a Project Request is approved. Nothing is reserved until then.</p>;
  else if (!saved) body = <p>Funds are checked when you save the draft.</p>;
  else if (error) body = <div role="alert"><p>{error}</p><button type="button" onClick={retry}>Try again</button></div>;
  else if (!check?.funding) body = <p role="status">Checking funds…</p>;
  else {
    const f = fundingFor(check.funding, estimate);
    const shortfall = f.shortfall ? decimalAbs(f.availableAfter) : null;
    const used = scaled(f.thisRequisition) - scaled(check.funding.already_reserved);
    const avail = scaled(check.funding.available);
    const pct = avail <= 0n ? (used > 0n ? 100 : 0) : Number((used < 0n ? 0n : used) * 100n / avail);
    body = <>
      <dl className="req-funds">
        <div><dt>Available Now</dt><dd><Money zero="value" value={check.funding.available} currency={currency} /></dd></div>
        <div><dt>This Requisition</dt><dd><Money zero="value" value={f.thisRequisition} currency={currency} /></dd></div>
        <div><dt>Available After</dt><dd><Money zero="value" value={f.availableAfter} currency={currency} /></dd></div>
      </dl>
      <div role="img" className="req-meter" aria-label={`This requisition ${formatMoney(f.thisRequisition, currency)} ${currency} against ${formatMoney(check.funding.available, currency)} ${currency} available`}><span style={{ inlineSize: `${Math.min(pct, 100)}%` }} data-over={f.shortfall || undefined} /></div>
      <p className="req-pill" data-tone={f.shortfall ? "danger" : "success"}>{f.shortfall ? <Warning aria-hidden="true" weight="regular" /> : <CheckCircle aria-hidden="true" weight="regular" />}{f.shortfall ? "Exceeds the available funds" : "Within the available funds"}</p>
      {shortfall && <p>This is {formatMoney(shortfall, currency)} {currency} over the funds available. A requisition over its funding cannot be submitted. Reduce the estimate, or ask for a budget transfer first.</p>}
    </>;
  }
  return <section className="req-card" id="funding-check" tabIndex={-1} aria-labelledby="funding-title">
    <header className="req-card-head"><h2 id="funding-title">Funding Check</h2></header>
    {body}
    {route && route.steps.length > 0 && <div className="req-route"><h3>Approval Route</h3>
      <ol>{route.steps.map(s => <li key={s.node_id}>{s.label}: {s.approver_emails.join(", ") || "no approver"} (step {route.steps.indexOf(s) + 1} of {route.steps.length})</li>)}</ol></div>}
  </section>;
}

function decimalAbs(value: string) { return value.startsWith("-") ? value.slice(1) : value; }
