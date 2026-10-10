import { useId, useState } from "react";
import type { components } from "../../api/generated/schema";
import { formatMoney } from "../../lib/money";
import Money from "../ui/Money";
import { chartUnits, share } from "./decimal";
type Node = components["schemas"]["TreeNode"];
const buckets = ["allocated", "reserved", "committed", "actual", "available"] as const;
export default function BarChart({ nodes }: { nodes: Node[] }) {
  const id = useId();
  const geometry = getComputedStyle(document.documentElement);
  const size = (token: string) => parseInt(geometry.getPropertyValue(token), 10);
  const rowHeight = size("--dashboard-chart-row-height");
  const patternStep = size("--dashboard-pattern-step");
  const patternBase = size("--space-2");
  const [hidden, setHidden] = useState<Set<string>>(() => new Set());
  const [tooltip, setTooltip] = useState<string | null>(null);
  const records = nodes.flatMap(node => node.balance ? buckets.map((bucket, series) => ({ name: `${node.number} ${node.name}`, bucket, series, value: node.balance![bucket], currency: node.balance!.currency })) : []);
  const amounts = records.map(r => chartUnits(r.value, r.currency));
  const maximum = (currency: string) => amounts.reduce((max, n, i) => records[i]!.currency === currency && (n < 0n ? -n : n) > max ? (n < 0n ? -n : n) : max, 0n);
  return <section aria-label="Child project bucket comparison"><h2 id={`${id}-name`}>Child Project Budgets</h2><p id={`${id}-description`}>Allocation, reservation, commitment, actual and available amounts for each child. Patterns and labels identify every bucket; currencies stay separate.</p>
    <div className="dashboard-legend" aria-label="Chart legend">{buckets.map(bucket => <button key={bucket} aria-pressed={!hidden.has(bucket)} onClick={() => setHidden(current => { const next = new Set(current); if (next.has(bucket)) next.delete(bucket); else next.add(bucket); return next; })}>{bucket}</button>)}</div>
    <p>Use the legend to hide or show chart buckets. The data table always includes every bucket.</p>
    {!records.length ? <p>No child project budgets to compare.</p> : <div className="dashboard-chart-table"><div className="dashboard-plot">
      <svg role="img" aria-labelledby={`${id}-name`} aria-describedby={`${id}-description`} width="100%" height={records.length * rowHeight}>
        <defs>{buckets.map((bucket, series) => <pattern key={bucket} id={`${id}-${bucket}`} width={patternBase + series * patternStep} height={patternBase + series * patternStep} patternUnits="userSpaceOnUse"><rect width="100%" height="100%" fill={`var(--chart-${series + 1})`} /><path d={series === 0 ? "M0 0L8 8" : series === 1 ? "M0 4H10" : "M0 0V12"} stroke="var(--surface)" strokeWidth="2" /></pattern>)}</defs>
        {[25, 50, 75].map(percent => <line key={percent} x1={`${percent}%`} x2={`${percent}%`} y1="0" y2="100%" stroke="var(--border)" />)}
        {records.map((r, i) => <g key={`${r.name}-${r.bucket}`} tabIndex={0} aria-label={`${r.name}: ${r.bucket} ${formatMoney(r.value, r.currency)} ${r.currency}`} onMouseEnter={() => setTooltip(`${r.name}: ${r.bucket} ${formatMoney(r.value, r.currency)} ${r.currency}`)} onMouseLeave={() => setTooltip(null)} onFocus={() => setTooltip(`${r.name}: ${r.bucket} ${formatMoney(r.value, r.currency)} ${r.currency}`)} onBlur={() => setTooltip(null)} visibility={hidden.has(r.bucket) ? "hidden" : undefined}>
          <title>{r.name}: {r.bucket} {formatMoney(r.value, r.currency)} {r.currency}</title>
          <text x="0" y={i * rowHeight + size("--space-4")}>{r.name} · {r.bucket}</text>
          <rect x="0" y={i * rowHeight + size("--space-6")} width={`${share(amounts[i]! < 0n ? -amounts[i]! : amounts[i]!, maximum(r.currency))}%`} height={size("--space-2")} fill={`url(#${id}-${r.bucket})`} stroke={`var(--chart-${r.series + 1})`} strokeDasharray={`var(--chart-${r.series + 1}-stroke)`} />
          <text className="chart-value" data-negative={r.value.startsWith("-") || undefined} data-value={r.value} data-currency={r.currency} x="100%" textAnchor="end" y={i * rowHeight + size("--space-8")}>{formatMoney(r.value, r.currency)} {r.currency}</text>
        </g>)}
      </svg><p role="status" className="dashboard-tooltip">{tooltip ?? "Focus or hover a bar for its amount."}</p></div>
      <table><caption>Child project budget data</caption><thead><tr><th>Project / bucket</th><th>Amount</th></tr></thead><tbody>{records.map(r => <tr key={`${r.name}-${r.bucket}`}><th scope="row">{r.name} / {r.bucket}</th><td className="chart-table-value" data-value={r.value} data-currency={r.currency}><Money value={r.value} currency={r.currency} /></td></tr>)}</tbody></table>
    </div>}<p>{nodes.length} child projects shown. Reservations, commitments and actuals consume the allocated budget.</p>
  </section>;
}
