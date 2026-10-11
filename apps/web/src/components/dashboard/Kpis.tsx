import type { components } from "../../api/generated/schema";
import Money from "../ui/Money";
import { consumptionPercent } from "../../lib/money";
import { label } from "../projects/api";
import { difference, decimal, scaled } from "./decimal";
type Buckets = components["schemas"]["SummaryCurrency"];
export default function Kpis({ balance, asOf, navigate, href }: { balance: Buckets; asOf: string; navigate?: (href: string) => void; href?: string }) {
  const values = { ...balance, variance: difference(balance.allocated, decimal(scaled(balance.reserved) + scaled(balance.committed) + scaled(balance.actual))), consumption: consumptionPercent(balance.allocated, balance.available) };
  return <div className="dashboard-kpis">{(["allocated", "reserved", "committed", "actual", "available", "variance", "consumption"] as const).map(bucket => {
    const target = href ? `${href}?bucket=${["allocated", "reserved", "committed", "actual"].includes(bucket) ? bucket : "all"}#ledger` : undefined;
    const content = <><span className="dashboard-kpi-label">{bucket === "consumption" ? "Consumption percentage" : ({ allocated: "Allocation", reserved: "Reservation", committed: "Commitment" } as Record<string, string>)[bucket] ?? label(bucket)}</span><strong>{bucket === "consumption" ? values[bucket] : <Money value={values[bucket]} currency={balance.currency} />}</strong><span>As of {asOf}</span></>;
    return target ? <a className="dashboard-kpi" key={bucket} href={target} onClick={e => { if (navigate && !e.metaKey && !e.ctrlKey) { e.preventDefault(); navigate(target); } }}>{content}</a> : <div className="dashboard-kpi" key={bucket}>{content}</div>;
  })}</div>;
}
