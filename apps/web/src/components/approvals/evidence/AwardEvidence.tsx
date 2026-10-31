import { ArrowRight } from "@phosphor-icons/react";
import { useEffect, useState } from "react";

import { apiClient } from "../../../api/client";
import type { components } from "../../../api/generated/schema";
import Money from "../../ui/Money";

/** The server's evidence for an award (sourcing module, frozen figures only). Rendered as given; the browser computes nothing. */
type Json = Record<string, unknown>;
type Vendor = { vendor_id: string; vendor_name: string; budget_total: string; total: string };
type Requisition = { requisition_id: string; requisition: string; scope: string; award: string; over_scope_by: string; release: string; return_project: string };
type Line = { line_no: number; vendor_name: string; justification?: string | null; tie_override_reason?: string | null };
type Exception = { vendor_id: string; exceptions: string };
type Check = components["schemas"]["RequisitionCheck"];
type Evaluators = components["schemas"]["EvaluationRead"]["evaluators"];
type Live<T> = { state: "loading" } | { state: "failed" } | { state: "ok"; value: T };

const arr = <T,>(value: unknown): T[] => (Array.isArray(value) ? (value as T[]) : []);
const BASIS: Record<string, string> = { lowest_cost_supplier: "Lowest Cost Supplier", best_average_cost: "Best Average Cost", manual: "Manual Selection" };
const NOT_AVAILABLE = { funds: "Funds are not available to you", evaluators: "Evaluator details are not available to you" };

export default function AwardEvidence({ evidence }: { evidence: Json }) {
  const rfqId = String(evidence.rfq_id);
  const currency = String(evidence.currency);
  const vendors = arr<Vendor>(evidence.totals_by_vendor);
  const requisitions = arr<Requisition>(evidence.requisitions);
  const notLowest = arr<Line>(evidence.not_lowest);
  const ties = arr<Line>(evidence.tie_overrides);
  const exceptions = arr<Exception>(evidence.exceptions);
  const weighted = ((evidence.evaluator_results as Json | null)?.weighted ?? null) as Record<string, string | null> | null;
  const rationale = (evidence.rationale as string | null) || undefined;
  const names = (evidence.vendor_names ?? {}) as Record<string, string>;
  const nameOf = (id: string) => names[id] || vendors.find((v) => v.vendor_id === id)?.vendor_name || "Vendor";

  const [checks, setChecks] = useState<Live<Check[]>>({ state: "loading" });
  const [evaluators, setEvaluators] = useState<Live<Evaluators>>({ state: "loading" });
  useEffect(() => {
    let live = true;
    const path = { params: { path: { id: rfqId } } };
    // The funds and the declarations move or are permissioned apart from the frozen evidence, so they are read live; a refusal degrades only its own part.
    void apiClient.GET("/api/v1/sourcing/rfqs/{id}/award", path).then((r) => live && setChecks(r.data ? { state: "ok", value: r.data.checks } : { state: "failed" }), () => live && setChecks({ state: "failed" }));
    void apiClient.GET("/api/v1/sourcing/rfqs/{id}/evaluation", path).then((r) => live && setEvaluators(r.data ? { state: "ok", value: r.data.evaluators } : { state: "failed" }), () => live && setEvaluators({ state: "failed" }));
    return () => { live = false; };
  }, [rfqId]);

  const funds = (id: string): Check | undefined => (checks.state === "ok" ? checks.value.find((c) => c.requisition_id === id) : undefined);
  const fundsCell = (id: string, pick: (c: Check) => string) => {
    if (checks.state === "loading") return "Loading";
    const c = funds(id);
    return c ? <Money value={pick(c)} currency={currency} zero="value" /> : NOT_AVAILABLE.funds;
  };
  const money = (value: string) => <Money value={value} currency={currency} zero="value" />;

  return (
    <section aria-labelledby="appr-award-evidence" className="appr-evidence">
      <h3 id="appr-award-evidence">Award Evidence</h3>
      <dl className="appr-facts">
        <div><dt>Basis</dt><dd>{BASIS[String(evidence.basis)] ?? String(evidence.basis)}</dd></div>
        {rationale && <div><dt>Rationale</dt><dd>{rationale}</dd></div>}
      </dl>

      <div className="appr-scroll" role="region" aria-label="By Vendor" tabIndex={0}>
      <table className="appr-evidence-table">
        <caption>By Vendor</caption>
        <thead><tr><th scope="col">Vendor</th><th scope="col" className="appr-num">Budget</th><th scope="col" className="appr-num">Total incl. tax &amp; freight</th></tr></thead>
        <tbody>
          {vendors.map((v) => (
            <tr key={v.vendor_id}>
              <th scope="row">{v.vendor_name}</th>
              <td className="appr-num">{money(v.budget_total)}</td>
              <td className="appr-num">{v.total !== v.budget_total ? money(v.total) : <span className="appr-meta">Same as Budget</span>}</td>
            </tr>
          ))}
        </tbody>
      </table>
      </div>

      <div className="appr-scroll" role="region" aria-label="Scope and Funds" tabIndex={0}>
      <table className="appr-evidence-table">
        <caption>Scope and Funds</caption>
        <thead>
          <tr>
            <th scope="col">Requisition</th><th scope="col" className="appr-num">Scope</th><th scope="col" className="appr-num">Award</th><th scope="col" className="appr-num">Funds</th>
            <th scope="col" className="appr-num">Over Scope By</th><th scope="col" className="appr-num">Over Funds By</th><th scope="col" className="appr-num">Released</th><th scope="col">Returned To</th>
          </tr>
        </thead>
        <tbody>
          {requisitions.map((r) => (
            <tr key={r.requisition_id}>
              <th scope="row">{r.requisition}</th>
              <td className="appr-num">{money(r.scope)}</td>
              <td className="appr-num">{money(r.award)}</td>
              <td className="appr-num">{fundsCell(r.requisition_id, (c) => c.funds)}</td>
              <td className="appr-num">{money(r.over_scope_by)}</td>
              <td className="appr-num">{fundsCell(r.requisition_id, (c) => c.over_funds_by)}</td>
              <td className="appr-num">{money(r.release)}</td>
              <td>{r.return_project || "No project"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      </div>

      {(notLowest.length > 0 || ties.length > 0) && (
        <section aria-labelledby="appr-award-notlowest">
          <h4 id="appr-award-notlowest">Not the Lowest</h4>
          <ul className="appr-evidence-docs">
            {notLowest.map((l) => <li key={`n${l.line_no}-${l.vendor_name}`}><strong>Line {l.line_no}, {l.vendor_name}</strong>: {l.justification || "No justification given"}</li>)}
            {ties.map((l) => <li key={`t${l.line_no}-${l.vendor_name}`}><strong>Line {l.line_no}, {l.vendor_name}, tie order overridden</strong>: {l.tie_override_reason}</li>)}
          </ul>
        </section>
      )}

      {exceptions.length > 0 && (
        <section aria-labelledby="appr-award-exceptions">
          <h4 id="appr-award-exceptions">Exceptions</h4>
          <ul className="appr-evidence-docs">{exceptions.map((e) => <li key={`${e.vendor_id}-${e.exceptions}`}><strong>{nameOf(e.vendor_id)}</strong>: {e.exceptions}</li>)}</ul>
        </section>
      )}

      <details className="appr-fold">
        <summary>Evaluators</summary>
        {weighted ? (
          <table className="appr-evidence-table">
            <caption>Weighted Score at Submission</caption>
            <thead><tr><th scope="col">Vendor</th><th scope="col" className="appr-num">Score</th></tr></thead>
            <tbody>{Object.entries(weighted).map(([id, score]) => <tr key={id}><th scope="row">{nameOf(id)}</th><td className="appr-num">{score ?? "Not scored"}</td></tr>)}</tbody>
          </table>
        ) : <p className="appr-meta">No weighted scores were recorded at submission.</p>}
        {evaluators.state === "loading" && <p className="appr-meta">Loading</p>}
        {evaluators.state === "failed" && <p className="appr-meta">{NOT_AVAILABLE.evaluators}</p>}
        {evaluators.state === "ok" && (
          <ul className="appr-evidence-docs" aria-label="Declarations">
            {(evaluators.value ?? []).map((e) => (
              <li key={e.identity_id}><strong>{e.display_name}</strong>: {e.declaration === "conflict" ? `Conflict declared${e.detail ? `, ${e.detail}` : ""}` : e.declaration === "clear" ? "No conflict" : "Not yet declared"}</li>
            ))}
            {!evaluators.value && <li>{NOT_AVAILABLE.evaluators}</li>}
          </ul>
        )}
      </details>

      <a className="appr-link" href={`/sourcing/rfqs/${rfqId}/award`}>Open the full award<ArrowRight aria-hidden="true" /></a>
    </section>
  );
}
