import { CaretDown, CaretRight, CheckCircle, WarningCircle, WarningOctagon } from "@phosphor-icons/react";
import { useState, type ReactNode } from "react";
import Money from "../../ui/Money";
import { formatDate } from "../../requisitions/api";
import { Pills } from "./VerdictStrip";
import { marksFor, type Comparison, type Vendor } from "./api";

type Tone = "success" | "warning" | "danger";
type Cell = { tone?: Tone; node: ReactNode };
const ICON = { success: CheckCircle, warning: WarningCircle, danger: WarningOctagon } as const;

/** Rows are keyed by the engine's `IDENTICAL_MEASURES`, so `identical_measures` hides exactly its own rows. */
type Row = { key: string; label: string; cell: (c: Comparison, v: Vendor) => Cell };
const money = (c: Comparison, value: string | null): ReactNode => value === null ? "—" : <Money value={value} currency={c.currency} zero="value" />;
const mark = (tone: Tone, text: string): Cell["node"] => { const Icon = ICON[tone]; return <span className="cmp-mark"><Icon aria-hidden="true" weight="regular" />{text}</span>; };
const ROWS: Record<string, Row[]> = {
  "Cost": [
    { key: "landed_total", label: "Landed Total", cell: (c, v) => { const m = marksFor(c, v.vendor_id).lowest; return v.landed_total === null ? { tone: "warning", node: mark("warning", "Not quoted") } : { tone: m ? "success" : undefined, node: <>{money(c, v.landed_total)}{m && mark("success", m)}</> }; } },
    { key: "difference_from_lowest", label: "Difference From Lowest", cell: (c, v) => v.difference_from_lowest === null ? { tone: "warning", node: mark("warning", "Not comparable") } : { node: money(c, v.difference_from_lowest) } },
  ],
  "Price, Tax and Freight": [
    { key: "price", label: "Price", cell: (c, v) => ({ node: money(c, v.price) }) },
    { key: "tax", label: "Tax and Duty", cell: (c, v) => ({ node: money(c, v.tax) }) },
    { key: "freight", label: "Freight", cell: (c, v) => ({ node: money(c, v.freight) }) },
  ],
  "Delivery": [
    { key: "lead_time_days", label: "Lead Time", cell: (c, v) => { const m = marksFor(c, v.vendor_id).fastest; return v.lead_time_days === null ? { node: "—" } : { tone: m ? "success" : undefined, node: <>{v.lead_time_days} days{m && mark("success", m)}</> }; } },
    { key: "meets_need", label: "Needed By", cell: (_c, v) => v.meets_need === null ? { node: "—" } : v.meets_need ? { node: <>{formatDate(v.ready_by)} {mark("success", "Meets needed-by date")}</> } : { tone: "danger", node: <>{formatDate(v.ready_by)} {mark("danger", "Misses needed-by date")}</> } },
  ],
  "Terms": [
    { key: "exceptions_count", label: "Exceptions", cell: (_c, v) => ({ node: v.exceptions_count }) },
  ],
  "Compliance and Score": [
    { key: "met", label: "Requirements Met", cell: (_c, v) => ({ node: `${v.met} of ${v.met_of} Met` }) },
    { key: "unmet", label: "Requirements Not Met", cell: (_c, v) => v.unmet >= 3 ? { tone: "danger", node: mark("danger", `${v.unmet} Not Met`) } : v.unmet > 0 ? { node: mark("warning", `${v.unmet} Not Met`) } : { node: "None" } },
    { key: "score", label: "Weighted Score", cell: (c, v) => { const m = marksFor(c, v.vendor_id).score; return v.score === null ? { tone: "warning", node: mark("warning", "Not available") } : { tone: m ? "success" : undefined, node: <>{v.score}{m && mark("success", m)}</> }; } },
  ],
};
const CLOSED = ["Price, Tax and Freight", "Terms"];

/** Layer 2: measures by vendor, `Show Only Differences` on by default. Narrow content shows two vendors and a picker that cannot choose one twice. */
export default function Matrix({ c, narrow, open }: { c: Comparison; narrow: boolean; open: (v: Vendor, opener: HTMLElement) => void }) {
  const [only, setOnly] = useState(true), [opened, setOpened] = useState<string[]>([]);
  const [pick, setPick] = useState<[string, string]>([c.vendors[0]?.vendor_id ?? "", c.vendors[1]?.vendor_id ?? ""]);
  const picker = narrow && c.vendors.length > 2;
  const shown = picker ? pick.map(id => c.vendors.find(v => v.vendor_id === id)).filter((v): v is Vendor => v !== undefined) : c.vendors;
  const hidden = only ? c.identical_rows : 0;
  const conversions = shown.filter(v => v.currency !== c.currency);
  return <section className="cmp-matrix" aria-labelledby="cmp-matrix-h">
    <div className="cmp-matrix-head"><h3 id="cmp-matrix-h">Side By Side</h3>
      <label className="cmp-check"><input type="checkbox" checked={only} onChange={e => setOnly(e.target.checked)} />Show Only Differences</label>
      {only && hidden > 0 && <span className="req-sub" role="status">{hidden} identical {hidden === 1 ? "row" : "rows"} hidden</span>}
    </div>
    <ul className="cmp-key" aria-label="Colour key"><li data-tone="success">{mark("success", "A win")}</li><li data-tone="warning">{mark("warning", "Worth a look")}</li><li data-tone="danger">{mark("danger", "A failure")}</li></ul>
    {picker && <div className="cmp-picker">{(["First Vendor", "Second Vendor"] as const).map((text, i) => <label key={text}>{text}
      <select value={pick[i]} onChange={e => setPick(i === 0 ? [e.target.value, pick[1]] : [pick[0], e.target.value])}>{c.vendors.filter(v => v.vendor_id === pick[i] || v.vendor_id !== pick[1 - i]).map(v => <option key={v.vendor_id} value={v.vendor_id}>{v.vendor_name}</option>)}</select></label>)}</div>}
    <div className="cmp-scroll"><table className="cmp-table">
      <caption className="visually-hidden">Bids side by side, one column per vendor</caption>
      <thead><tr><th scope="col"><span className="visually-hidden">Measure</span></th>{shown.map(v => <th key={v.vendor_id} scope="col"><button type="button" onClick={e => open(v, e.currentTarget)}>{v.vendor_name}</button><span className="cmp-pills"><Pills v={v} /></span>
        {v.currency !== c.currency && <span className="req-sub">{v.currency}, Converted at {v.conversion.rate}</span>}</th>)}</tr></thead>
      {Object.entries(ROWS).map(([group, rows]) => {
        const visible = rows.filter(r => !only || !c.identical_measures.includes(r.key));
        const expanded = !CLOSED.includes(group) || opened.includes(group), id = `cmp-group-${group.replaceAll(" ", "-").toLowerCase()}`;
        return <tbody key={group} id={id}><tr className="cmp-group"><th scope="colgroup" colSpan={shown.length + 1}>
          {!CLOSED.includes(group) ? <strong>{group}</strong> : <button type="button" aria-expanded={expanded} aria-controls={id} onClick={() => setOpened(o => o.includes(group) ? o.filter(g => g !== group) : [...o, group])}>
            {expanded ? <CaretDown aria-hidden="true" weight="regular" /> : <CaretRight aria-hidden="true" weight="regular" />}{group}{!expanded && <span className="req-sub"> {rows.length} {rows.length === 1 ? "row" : "rows"}</span>}</button>}</th></tr>
          {expanded && visible.map(r => <tr key={r.key}><th scope="row">{r.label}</th>{shown.map(v => { const cell = r.cell(c, v); return <td key={v.vendor_id} data-tone={cell.tone}>{cell.node}</td>; })}</tr>)}
        </tbody>;
      })}
    </table></div>
    {conversions.length > 0 && <p className="req-sub">Foreign bids are converted for comparison only. The vendor's bid is never altered.</p>}
  </section>;
}
