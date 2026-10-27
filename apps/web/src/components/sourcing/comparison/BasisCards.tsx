import { CheckCircle, Info, WarningOctagon } from "@phosphor-icons/react";
import Money from "../../ui/Money";
import { BASES, lineNumbers, vendorsOf, type BasisName, type Comparison } from "./api";

/** Pages/rfq-comparison.md 3: a pair of radio cards, each with its own server result, then the notices for the chosen one. */
export default function BasisCards({ c, basis, onChange }: { c: Comparison; basis: BasisName; onChange: (next: BasisName) => void }) {
  const chosen = c[basis], pairs = c.vendors.filter(v => vendorsOf(chosen).some(b => b.vendor_id === v.vendor_id) && v.unmet > 0);
  const insight = c.insight, split = insight?.dearer_basis === "best_average_cost";
  return <section className="cmp-basis" aria-labelledby="cmp-basis-h">
    <h3 id="cmp-basis-h">Award Basis</h3>
    <div role="radiogroup" aria-labelledby="cmp-basis-h" className="cmp-radios">{BASES.map(([name, text, help]) => {
      const result = c[name];
      return <label key={name} className="cmp-radio" data-checked={basis === name || undefined}>
        <input type="radio" name="cmp-basis" value={name} checked={basis === name} onChange={() => onChange(name)} />
        <span className="cmp-radio-body"><strong>{text}</strong><span className="req-sub">{help}</span>
          <span className="cmp-radio-result">{result.total === null ? "No vendor quoted every line" : <><Money value={result.total} currency={c.currency} zero="value" /><span> · {vendorsOf(result).length} {vendorsOf(result).length === 1 ? "vendor" : "vendors"}</span></>}</span></span>
      </label>;
    })}</div>
    {insight && <p className="cmp-insight" role="status"><Info aria-hidden="true" weight="regular" />{split
      ? <span>Splitting the award is <Money value={insight.difference} currency={c.currency} zero="value" />{insight.difference_pct !== null && ` (${insight.difference_pct}%)`} cheaper than the best single vendor, and uses {insight.lowest_cost_supplier_vendors} vendors instead of one.</span>
      : <span>The best single vendor is <Money value={insight.difference} currency={c.currency} zero="value" />{insight.difference_pct !== null && ` (${insight.difference_pct}%)`} cheaper than splitting the award.</span>}</p>}
    {chosen.within_scope === true && chosen.to_spare !== null && chosen.to_spare !== undefined && <p className="req-pill" data-tone="success"><CheckCircle aria-hidden="true" weight="regular" />Within approved scope, <Money value={chosen.to_spare} currency={c.currency} zero="value" /> to spare</p>}
    {chosen.within_scope === false && chosen.over_by !== null && <p className="req-pill" data-tone="danger"><WarningOctagon aria-hidden="true" weight="regular" />Exceeds approved scope by <Money value={chosen.over_by} currency={c.currency} zero="value" /></p>}
    {(chosen.not_lowest_count ?? 0) > 0 && <p className="cmp-note">Not the lowest on {chosen.not_lowest_count} {chosen.not_lowest_count === 1 ? "line" : "lines"} ({lineNumbers(c, chosen.not_lowest_lines ?? [])}). A justification will be asked for at award.</p>}
    {pairs.map(v => <p key={v.vendor_id} className="cmp-note">Requirements: {v.vendor_name} {v.unmet} {v.unmet === 1 ? "requirement" : "requirements"} not met</p>)}
  </section>;
}

/** The sticky draft bar. `Start Award` joins it when E12-S16 exists; until then the page only states the draft. */
export function AwardBar({ c, basis }: { c: Comparison; basis: BasisName }) {
  const chosen = c[basis], text = BASES.find(b => b[0] === basis)![1], n = vendorsOf(chosen).length;
  return <div className="cmp-award" role="region" aria-label="Award draft">
    <span>Award draft: {text} · {n} {n === 1 ? "vendor" : "vendors"} · {chosen.total === null ? "no total" : <Money value={chosen.total} currency={c.currency} zero="value" />}</span>
    {chosen.partial && <span className="req-sub">Some lines have no quote in this selection.</span>}
  </div>;
}
