import { CheckCircle, Tag } from "@phosphor-icons/react";
import { useState } from "react";
import { apiClient } from "../../../api/client";
import Money from "../../ui/Money";
import { failure } from "../../requisitions/api";
import { useLoad } from "../../requisitions/useLoad";
import Tabs from "./Tabs";
import { vendorsOf, type BasisName, type Comparison, type Evaluation } from "./api";

/** `By Line`: each line by vendor (the server's landed line and its converted unit cost, `Cell.unit_price_converted`; the page divides nothing), and an `In This Selection` row that is the server's per-vendor total. */
export function ByLine({ c, basis }: { c: Comparison; basis: BasisName }) {
  const chosen = c[basis];
  return <div className="cmp-scroll"><table className="cmp-table">
    <caption className="visually-hidden">Each line by vendor, landed and unit cost in {c.currency}</caption>
    <thead><tr><th scope="col">Line</th>{c.vendors.map(v => <th key={v.vendor_id} scope="col">{v.vendor_name}</th>)}</tr></thead>
    <tbody>{c.lines.map(l => <tr key={l.rfq_line_id}><th scope="row">{l.line_no}. {l.description}<span className="req-sub">Quantity {l.quantity}</span></th>
      {c.vendors.map(v => {
        const cell = l.cells.find(x => x.vendor_id === v.vendor_id);
        const lowest = l.lowest?.vendor_id === v.vendor_id, picked = vendorsOf(chosen).some(b => b.vendor_id === v.vendor_id && b.lines.includes(l.rfq_line_id));
        return <td key={v.vendor_id} data-tone={lowest ? "success" : cell?.not_quoted ? "warning" : undefined}>
          {!cell || cell.not_quoted || cell.landed === null ? "Not quoted" : <Money value={cell.landed} currency={c.currency} zero="value" />}
          {cell && !cell.not_quoted && cell.unit_price_converted !== null && <span className="req-sub">Unit Cost <Money value={cell.unit_price_converted} currency={c.currency} zero="value" /></span>}
          {lowest && <span className="cmp-mark"><Tag aria-hidden="true" weight="regular" />{l.lowest!.tied ? "Lowest, Tied" : "Lowest"}</span>}
          {picked && <span className="cmp-mark"><CheckCircle aria-hidden="true" weight="regular" />In this selection</span>}
        </td>;
      })}</tr>)}</tbody>
    <tfoot><tr><th scope="row">In This Selection</th>{c.vendors.map(v => { const b = vendorsOf(chosen).find(x => x.vendor_id === v.vendor_id); return <td key={v.vendor_id}>{b ? <Money value={b.total} currency={c.currency} zero="value" /> : "—"}</td>; })}</tr></tfoot>
  </table>{c.identical_lines > 0 && <p className="req-sub">{c.identical_lines} {c.identical_lines === 1 ? "line is" : "lines are"} priced identically by every vendor.</p>}</div>;
}

const PARTS = [["price", "Price"], ["tax", "Tax and Duty"], ["freight", "Freight"]] as const;

/** `Cost Breakdown`: a stacked bar per vendor (geometry only; every label is the server's figure) and the `Table` that is its accessible equivalent (A11Y-009). */
export function CostBreakdown({ c }: { c: Comparison }) {
  const [view, setView] = useState<"chart" | "table">("chart");
  const full = c.vendors.filter(v => v.landed_total !== null);
  const scale = Math.max(Number(c.approved_scope), ...full.map(v => Number(v.landed_total))) || 1;
  const pct = (value: string | null) => `${(Number(value ?? 0) / scale) * 100}%`;
  return <Tabs label="Cost breakdown views" idPrefix="cmp-cost" tabs={[["chart", "Chart"], ["table", "Table"]] as const} value={view} onChange={setView}>
    {view === "chart" ? <figure className="cmp-chart">
      <figcaption className="req-sub">Price, tax and duty, and freight per vendor. The dashed line is the approved scope, <Money value={c.approved_scope} currency={c.currency} zero="value" />. The Table tab holds the same values.</figcaption>
      <ul className="cmp-legend" aria-hidden="true">{PARTS.map(([key, text], i) => <li key={key}><span className="cmp-swatch" data-part={i} />{text}</li>)}</ul>
      {full.map(v => <div key={v.vendor_id} className="cmp-bar-row">
        <span className="cmp-bar-name">{v.vendor_name}</span>
        <div className="cmp-track" role="img" aria-label={`${v.vendor_name}: price ${v.price}, tax and duty ${v.tax}, freight ${v.freight}, total ${v.landed_total} ${c.currency}`}>
          {PARTS.map(([key], i) => <span key={key} className="cmp-seg" data-part={i} style={{ width: pct(v[key]) }} />)}
          <span className="cmp-scope-line" style={{ insetInlineStart: pct(c.approved_scope) }} />
        </div>
        <span className="cmp-bar-total"><Money value={v.landed_total} currency={c.currency} zero="value" /></span>
      </div>)}
    </figure> : <div className="cmp-scroll"><table className="cmp-table">
      <caption className="visually-hidden">Cost breakdown per vendor in {c.currency}</caption>
      <thead><tr><th scope="col">Vendor</th>{PARTS.map(([key, text]) => <th key={key} scope="col">{text}</th>)}<th scope="col">Total</th></tr></thead>
      <tbody>{full.map(v => <tr key={v.vendor_id}><th scope="row">{v.vendor_name}</th>{PARTS.map(([key]) => <td key={key}><Money value={v[key]} currency={c.currency} zero="value" /></td>)}<td><Money value={v.landed_total} currency={c.currency} zero="value" /></td></tr>)}</tbody>
      <tfoot><tr><th scope="row">Approved Scope</th><td colSpan={4}><Money value={c.approved_scope} currency={c.currency} zero="value" /></td></tr></tfoot>
    </table></div>}
  </Tabs>;
}

/** `Scoring`: criteria, weights and the weighted score, as the server returns them to this user. Declarations come only to `rfq.edit` holders; a section the server withholds is omitted. */
export function Scoring({ c, id }: { c: Comparison; id: string }) {
  const read = useLoad(async (): Promise<Evaluation> => {
    const { data, error } = await apiClient.GET("/api/v1/sourcing/rfqs/{id}/evaluation", { params: { path: { id } } });
    if (!data) throw new Error(failure(error, "Loading the scoring"));
    return data;
  }, [id]);
  if (read.error) return <div role="alert"><p>{read.error}</p><button type="button" onClick={read.reload}>Try again</button></div>;
  if (!read.data) return <p className="req-skeleton req-skeleton-short" role="status" aria-label="Loading scoring" />;
  const e = read.data;
  return <div className="cmp-scoring">
    <div className="cmp-scroll"><table className="cmp-table">
      <caption className="visually-hidden">Criteria and weights</caption>
      <thead><tr><th scope="col">Criterion</th><th scope="col">Weight</th></tr></thead>
      <tbody>{e.criteria.map(k => <tr key={k.id ?? k.position}><th scope="row">{k.name}</th><td className="numeric">{k.weight}%</td></tr>)}</tbody>
    </table></div>
    <div className="cmp-scroll"><table className="cmp-table">
      <caption className="visually-hidden">Scores by criterion and vendor, as the server returns them</caption>
      <thead><tr><th scope="col">Criterion</th>{c.vendors.map(v => <th key={v.vendor_id} scope="col">{v.vendor_name}</th>)}</tr></thead>
      <tbody>{e.criteria.map(k => <tr key={k.id ?? k.position}><th scope="row">{k.name}</th>{c.vendors.map(v => {
        const given = e.scores.filter(x => x.criterion_id === k.id && x.vendor_id === v.vendor_id);
        return <td key={v.vendor_id} className="numeric">{given.length ? given.map(x => x.score).join(", ") : "Not available"}</td>;
      })}</tr>)}</tbody>
    </table></div>
    <div className="cmp-scroll"><table className="cmp-table">
      <caption className="visually-hidden">Weighted score per vendor</caption>
      <thead><tr><th scope="col">Vendor</th><th scope="col">Weighted Score</th></tr></thead>
      <tbody>{c.vendors.map(v => <tr key={v.vendor_id}><th scope="row">{v.vendor_name}</th><td className="numeric">{e.weighted?.find(w => w.vendor_id === v.vendor_id)?.score ?? "Not available"}</td></tr>)}</tbody>
    </table></div>
    {e.evaluators && <section aria-labelledby="cmp-evaluators-h"><h3 id="cmp-evaluators-h">Evaluators</h3>
      <ul className="vendor-list-plain">{e.evaluators.map(x => <li key={x.identity_id}>{x.display_name}: {x.declaration === "conflict" ? `Conflict declared${x.detail ? `, ${x.detail}` : ""}` : x.declaration === "clear" ? "No conflict" : "Not declared yet"}{x.submitted ? ", scores submitted" : ""}</li>)}</ul></section>}
  </div>;
}
