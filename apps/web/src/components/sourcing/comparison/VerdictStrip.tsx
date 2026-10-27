import { Clock, Star, Tag, WarningCircle } from "@phosphor-icons/react";
import Money from "../../ui/Money";
import { marksFor, type Comparison, type Vendor } from "./api";

export function Pills({ v }: { v: Vendor }) {
  return <>
    {v.late && <span className="req-pill" data-tone="warning"><WarningCircle aria-hidden="true" weight="regular" />Late, Accepted By Override</span>}
    {!v.full && <span className="req-pill" data-tone="warning"><WarningCircle aria-hidden="true" weight="regular" />Partial</span>}
  </>;
}

/** Layer 1: who is cheapest, fastest, safest. Every figure is the server's. */
export default function VerdictStrip({ c, open }: { c: Comparison; open: (v: Vendor, opener: HTMLElement) => void }) {
  return <ul className="cmp-verdicts" aria-label="Vendors at a glance" tabIndex={0}>{c.vendors.map(v => {
    const m = marksFor(c, v.vendor_id);
    return <li key={v.vendor_id} className="req-card cmp-verdict">
      <button type="button" className="cmp-vendor-button" onClick={e => open(v, e.currentTarget)}>{v.vendor_name}</button>
      <span className="cmp-pills"><Pills v={v} /></span>
      <span className="cmp-total">{v.landed_total === null ? "Not quoted" : <Money value={v.landed_total} currency={c.currency} zero="value" />}</span>
      <span className="req-sub">{v.difference_from_lowest !== null ? <>Difference from lowest <Money value={v.difference_from_lowest} currency={c.currency} zero="value" /></> : "Not comparable"}</span>
      <span>{v.lead_time_days === null ? "No lead time" : `${v.lead_time_days} days lead time`}</span>
      <span>{v.met} of {v.met_of} Met</span>
      <span>Score {v.score ?? "Not available"}</span>
      <span className="cmp-marks">{m.lowest && <span className="cmp-mark"><Tag aria-hidden="true" weight="regular" />{m.lowest}</span>}{m.fastest && <span className="cmp-mark"><Clock aria-hidden="true" weight="regular" />{m.fastest}</span>}{m.score && <span className="cmp-mark"><Star aria-hidden="true" weight="regular" />{m.score}</span>}</span>
    </li>;
  })}</ul>;
}
