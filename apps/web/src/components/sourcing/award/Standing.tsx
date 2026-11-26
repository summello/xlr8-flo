import { CheckCircle, WarningOctagon } from "@phosphor-icons/react";
import Money from "../../ui/Money";
import { verdict, type Award } from "./api";

/** The deciding figure and its verdict side by side (decision 156): both read the one `checks[]` selector. */
export default function Standing({ award }: { award: Award }) {
  const v = verdict(award.checks), c = v.check, ccy = award.currency;
  return <span className="aw-standing">
    <span><span className="req-sub">Total Awarded</span><Money value={award.total} currency={ccy} zero="value" /></span>
    {v.kind === "ok" && <span className="req-pill" data-tone="success" role="status"><CheckCircle aria-hidden="true" weight="regular" />Within scope and funds</span>}
    {c && <span className="req-pill" data-tone="danger" role="status"><WarningOctagon aria-hidden="true" weight="regular" />
      {v.kind === "scope" ? "Over scope by" : "Over reserved funds by"}&nbsp;<Money value={v.kind === "scope" ? c.over_scope_by : c.over_funds_by} currency={ccy} zero="value" />&nbsp;on {c.requisition}</span>}
  </span>;
}
