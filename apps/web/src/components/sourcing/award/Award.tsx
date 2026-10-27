import { useRef, useState } from "react";
import { apiClient } from "../../../api/client";
import { failure, mutationHeaders, type Problem } from "../../requisitions/api";
import SubmitSummary from "../../requisitions/SubmitSummary";
import { formatMoney } from "../../../lib/money";
import type { SummaryProblem } from "../../requisitions/model";
import type { Comparison } from "../comparison/api";
import { linesBody, lowestQty, nonZero, problemOf, rowKey, verdict, type Award as AwardRead, type Row } from "./api";
import Checks from "./Checks";
import Lines from "./Lines";
import Standing from "./Standing";

type Failure = { title: string; lead: string; problems: SummaryProblem[] };
const NO_WORKFLOW = "No award workflow is published. Ask an administrator to set one up. Your draft is kept.";

/** pages/sourcing.md 2: the award is a draft that passes three checks. Every figure, verdict and release amount is the server's. */
export default function Award({ id, award, c, onAward, navigate }: { id: string; award: AwardRead; c: Comparison; onAward: (a: AwardRead) => void; navigate: (href: string) => void }) {
  const [rows, setRows] = useState<Record<string, Row>>(() => Object.fromEntries(award.lines.map(l => [rowKey(l.rfq_line_id, l.vendor_id), { quantity: l.quantity, reason: l.justification ?? l.tie_override_reason ?? "" }])));
  const [rationale, setRationale] = useState(award.rationale ?? "");
  const [problem, setProblem] = useState<Failure | null>(null), [busy, setBusy] = useState(false), [saved, setSaved] = useState(false);
  const key = useRef<string | null>(null), locked = award.status !== "draft", path = { params: { path: { id } } };
  const setRow = (k: string, patch: Partial<Row>) => setRows(r => ({ ...r, [k]: { quantity: "", reason: "", ...r[k], ...patch } }));
  const lowest = () => setRows(Object.fromEntries(c.lines.filter(l => l.lowest).flatMap(l => l.cells.map(cell => [rowKey(l.rfq_line_id, cell.vendor_id), { quantity: cell.vendor_id === l.lowest!.vendor_id ? lowestQty(l.quantity, cell.quantity) : "", reason: "" }]))));
  const fail = (title: string, error: Problem | undefined, action: string) => setProblem({ title, lead: failure(error, action), problems: (error?.errors ?? []).map(e => problemOf(c, e)) });

  /** Lines, then rationale, each under the version the last write returned (§O). Null on a refusal, with the summary set. */
  const save = async (): Promise<AwardRead | null> => {
    const put = await apiClient.PUT("/api/v1/sourcing/rfqs/{id}/award/lines", { ...path, headers: mutationHeaders(crypto.randomUUID(), award.version), body: { lines: linesBody(c, rows) } });
    if (!put.data) { fail("The lines were not saved", put.error as Problem | undefined, "Saving the lines"); return null; }
    const patch = await apiClient.PATCH("/api/v1/sourcing/rfqs/{id}/award", { ...path, headers: mutationHeaders(crypto.randomUUID(), put.data.version), body: { rationale: rationale.trim() || null } });
    if (!patch.data) { onAward(put.data); fail("The rationale was not saved", patch.error as Problem | undefined, "Saving the rationale"); return null; }
    onAward(patch.data);
    return patch.data;
  };
  const run = async (submit: boolean) => {
    setBusy(true); setProblem(null); setSaved(false);
    try {
      const draft = await save();
      if (!draft || !submit) { setSaved(draft !== null); return; }
      key.current ??= crypto.randomUUID();
      const { data, error, response } = await apiClient.POST("/api/v1/sourcing/rfqs/{id}/award/submit", { ...path, headers: mutationHeaders(key.current) });
      if (data) { key.current = null; onAward(data); return; }
      const e = error as Problem | undefined;
      if (response?.status === 409 && e?.checks?.problem === "workflow_not_set_up") setProblem({ title: "No approval workflow", lead: NO_WORKFLOW, problems: [] });
      else if (response?.status === 409 && e?.checks?.problem === "over_scope") {
        const k = e.checks, over = nonZero(k.over_scope_by ?? "0") ? `Over scope by ${formatMoney(k.over_scope_by!, draft.currency)} ${draft.currency}` : `Over reserved funds by ${formatMoney(k.over_funds_by!, draft.currency)} ${draft.currency}`;
        setProblem({ title: "The award is over its limit", lead: `${over} on ${k.requisition}. Change the selection above, or amend the requisition. Your selections are kept.`, problems: [] });
      } else fail("The award was not submitted", e, "Submitting the award");
    } catch { fail("The award was not submitted", undefined, "Submitting the award"); } finally { setBusy(false); }
  };

  const v = verdict(award.checks);
  return <form className="aw" noValidate onSubmit={e => { e.preventDefault(); void run(true); }} aria-busy={busy || undefined}>
    {problem && <SubmitSummary title={problem.title} lead={problem.lead} problems={problem.problems} />}
    {locked && <p className="req-pill" role="status" data-tone="info">{award.status === "approval_pending" ? "Approval Pending" : award.status.replaceAll("_", " ")}: this award can no longer be edited here.</p>}
    <div className="aw-top"><h3>Award {award.number}</h3><Standing award={award} />
      <button type="button" disabled={locked} onClick={lowest}>Set All to Lowest</button></div>
    <Lines c={c} rows={rows} setRow={setRow} required={award.justification_required === true} locked={locked} />
    <div className="req-field"><label className="t-label" htmlFor="aw-rationale">Award Rationale<span className="req-tag"><span className="rq-need">Required</span></span></label>
      <textarea id="aw-rationale" rows={4} maxLength={2000} disabled={locked} value={rationale} onChange={e => setRationale(e.target.value)} aria-describedby="aw-rationale-help" />
      <p className="field-help" id="aw-rationale-help">Why this selection. Kept with the award for the approvers.</p></div>
    <Checks award={award} />
    <div className="cmp-award aw-bar" role="region" aria-label="Award actions">
      <Standing award={award} />
      <span role="status" className="req-sub">{saved ? "Draft saved." : v.kind === "ok" ? "" : "Figures are as of the last save."}</span>
      <button type="button" disabled={locked || busy} onClick={() => void run(false)}>Save Draft</button>
      <button type="submit" disabled={locked || busy}>Submit for Approval</button>
      <a className="req-link-button" href={`/sourcing/rfqs/${id}/comparison`} onClick={e => { e.preventDefault(); navigate(`/sourcing/rfqs/${id}/comparison`); }}>Back to the Comparison</a>
    </div>
  </form>;
}
