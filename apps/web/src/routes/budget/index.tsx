import { useCallback, useEffect, useState } from "react";
import { apiClient } from "../../api/client";
import type { components } from "../../api/generated/schema";
import Kpis from "../../components/dashboard/Kpis";
import Money from "../../components/ui/Money";
import { failure, label } from "../../components/projects/api";
import "../../styles/projects.css";
import "../../styles/dashboard.css";
export default function BudgetOverview({ navigate }: { navigate: (href: string) => void }) {
  const unitId = new URLSearchParams(window.location.search).get("unit_id") ?? undefined;
  const [summary, setSummary] = useState<components["schemas"]["BudgetSummary"] | null>(null);
  const [units, setUnits] = useState<components["schemas"]["OrgUnitPage"] | null>(null);
  const [errors, setErrors] = useState<string[]>([]);
  const [asOf, setAsOf] = useState("");
  const load = useCallback(async () => {
    const problems: string[] = [];
    await Promise.allSettled([
      apiClient.GET("/api/v1/budget/summary", { params: { query: { unit_id: unitId } } }).then(r => { if (r.data) { setSummary(r.data); setAsOf(new Date().toLocaleString()); } else problems.push(failure(r.error, "Loading budget summary")); }),
      apiClient.GET("/api/v1/org/units").then(r => r.data ? setUnits(r.data) : problems.push(failure(r.error, "Loading units"))),
    ].map(promise => promise.catch(() => { problems.push(failure(undefined, "Loading budget")); })));
    setErrors(problems);
  }, [unitId]);
  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => { void load(); }, [load]);
  const moreUnits = async () => {
    if (!units?.next_cursor) return;
    try {
    const result = await apiClient.GET("/api/v1/org/units", { params: { query: { cursor: units.next_cursor } } });
    if (result.data) setUnits(current => ({ rows: [...(current?.rows ?? []), ...result.data!.rows], next_cursor: result.data!.next_cursor }));
    else setErrors([failure(result.error, "Loading more units")]);
    } catch { setErrors([failure(undefined, "Loading more units")]); }
  };
  return <div className="dashboard"><label htmlFor="budget-unit">Business / operating unit</label><select aria-describedby="budget-unit-help" id="budget-unit" value={unitId ?? ""} onChange={e => navigate(e.target.value ? `/budget?unit_id=${e.target.value}` : "/budget")}><option value="">Organization</option>{unitId && !units?.rows.some(unit => unit.id === unitId) && <option value={unitId}>Selected unit ({unitId})</option>}{units?.rows.map(unit => <option key={unit.id} value={unit.id}>{unit.name}</option>)}</select>{units?.next_cursor && <button onClick={() => void moreUnits()}>Load more units</button>}
    <p id="budget-unit-help">Select a unit to show the budgets in its authorization scope. Currencies are reported separately without conversion.</p>
    <a href={unitId ? `/projects?bu_id=${unitId}` : "/projects"} onClick={e => { e.preventDefault(); navigate(e.currentTarget.getAttribute("href")!); }}>Drill down to projects</a>
    {!summary && !errors.length && <div className="dashboard-loading" role="status">Loading budget summary…</div>}
    {!!errors.length && <div role="alert">{errors.map((error, i) => <p key={i}>{error}</p>)}<button onClick={() => void load()}>Retry budget summary</button></div>}
    {summary?.totals.length === 0 && <p>No allocation yet. No project budgets exist in this scope.</p>}
    {summary?.totals.map(total => <section key={total.currency} aria-label={`${total.currency} budget`}><h2>{total.currency} Budget</h2><Kpis balance={total} asOf={asOf} /><div className="dashboard-table-wrap"><table><caption>{total.currency} totals in the selected scope</caption><thead><tr><th>Bucket</th><th>Amount</th></tr></thead><tbody>{(["allocated", "reserved", "committed", "actual", "available"] as const).map(bucket => <tr key={bucket}><th scope="row">{label(bucket)}</th><td><Money value={total[bucket]} currency={total.currency} /></td></tr>)}</tbody></table></div></section>)}
  </div>;
}
