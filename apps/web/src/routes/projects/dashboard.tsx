import { useCallback, useEffect, useState } from "react";
import { WarningCircle, CheckCircle, Question } from "@phosphor-icons/react";
import { apiClient } from "../../api/client";
import type { components } from "../../api/generated/schema";
import { failure, label } from "../../components/projects/api";
import Kpis from "../../components/dashboard/Kpis";
import Hierarchy from "../../components/dashboard/Hierarchy";
import BarChart from "../../components/dashboard/BarChart";
import Money from "../../components/ui/Money";
import Ledger from "../../components/dashboard/Ledger";
import "../../styles/projects.css";
import "../../styles/dashboard.css";
export default function Dashboard({ id, navigate, onLoaded }: { id: string; navigate: (href: string) => void; onLoaded: (project: components["schemas"]["ProjectRead"], unit: string) => void }) {
  const [project, setProject] = useState<components["schemas"]["ProjectRead"] | null>(null);
  const [balance, setBalance] = useState<components["schemas"]["BalanceQueryRead"] | null>(null);
  const [tree, setTree] = useState<components["schemas"]["TreeRead"] | null>(null);
  const [risks, setRisks] = useState<components["schemas"]["RiskIndicatorsRead"] | null>(null);
  const [aggregate, setAggregate] = useState<components["schemas"]["AggregateRead"] | null>(null);
  const [errors, setErrors] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const load = useCallback(async () => {
    const problems: string[] = [];
    await Promise.allSettled([
      apiClient.GET("/api/v1/projects/{id}", { params: { path: { id } } }).then(r => { if (r.data) { setProject(r.data); onLoaded(r.data, r.data.bu_name ?? r.data.bu_id); } else problems.push(failure(r.error, "Loading project")); }),
      apiClient.GET("/api/v1/projects/{project_id}/balance", { params: { path: { project_id: id } } }).then(r => r.data ? setBalance(r.data) : problems.push(failure(r.error, "Loading balance"))),
      apiClient.GET("/api/v1/projects/{project_id}/balance/aggregate", { params: { path: { project_id: id } } }).then(r => r.data ? setAggregate(r.data) : problems.push(failure(r.error, "Loading aggregate"))),
      apiClient.GET("/api/v1/projects/{id}/tree", { params: { path: { id }, query: { include: "balances" } } }).then(r => r.data ? setTree(r.data) : problems.push(failure(r.error, "Loading hierarchy"))),
      apiClient.GET("/api/v1/projects/{id}/risk-indicators", { params: { path: { id } } }).then(r => r.data ? setRisks(r.data) : problems.push(failure(r.error, "Loading risk indicators"))),
    ].map(promise => promise.catch(() => { problems.push(failure(undefined, "Loading dashboard")); })));
    setErrors(problems); setLoading(false);
  }, [id, onLoaded]);
  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => { void load(); }, [load]);
  const queryBucket = new URLSearchParams(window.location.search).get("bucket");
  const bucket = queryBucket && ["allocated", "reserved", "committed", "actual"].includes(queryBucket) ? queryBucket as components["schemas"]["LedgerBucket"] : undefined;
  return <div className="dashboard">
    <header className="dashboard-record">{project && <><p><a href="/budget" onClick={e => { e.preventDefault(); navigate("/budget"); }}>Organization budget</a> / <a href={`/budget?unit_id=${project.bu_id}`} onClick={e => { e.preventDefault(); navigate(e.currentTarget.getAttribute("href")!); }}>{project.bu_name ?? "Business unit"}</a></p><h2>{project.number} {project.name}</h2><a href={`/projects/${id}`} onClick={e => { e.preventDefault(); navigate(`/projects/${id}`); }}>Project details</a></>}</header>
    <div className="dashboard-load-status" aria-live="polite">{loading && <p>Loading dashboard… Previously loaded figures are preserved.</p>}</div>{loading && !balance && <div className="dashboard-kpis" aria-hidden="true">{Array.from({ length: 7 }, (_, i) => <div className="dashboard-kpi-skeleton" key={i} />)}</div>}
    {errors.length > 0 && <div role="alert"><p>Dashboard partially available.</p>{errors.map((error, i) => <p key={i}>{error}</p>)}<button onClick={() => { setLoading(true); void load(); }}>Retry dashboard</button></div>}
    {balance && <><Kpis balance={balance} asOf={balance.as_of} href={`/projects/${id}/dashboard`} navigate={navigate} />{/^0(?:\.0*)?$/.test(balance.allocated) && <p>No budget allocated yet.</p>}</>}
    <div className="dashboard-panels"><section className="dashboard-card"><h2>Project Hierarchy</h2>{tree && <><Hierarchy tree={tree.tree} selected={id} navigate={navigate} />{tree.truncated && <p>The hierarchy is truncated to 500 nodes. Open a descendant to continue.</p>}</>}{aggregate && <p>{aggregate.node_count} projects in this subtree · {label(aggregate.mode)} funding. Totals remain separate by currency.</p>}</section>
    <section className="dashboard-card dashboard-risks"><h2>Risk Indicators</h2>{risks?.indicators.map(indicator => { const Icon = indicator.level === "unknown" ? Question : indicator.level === "low" ? CheckCircle : WarningCircle; return <section key={indicator.code}><h3>{label(indicator.code)}</h3><p><Icon aria-hidden="true" weight="regular" /> {label(indicator.level)}</p><dl>{indicator.facts.map((fact, i) => <div key={i}><dt>{fact.label}</dt><dd>{balance && ["allocated", "consumed"].includes(fact.label) ? <Money value={fact.value} currency={balance.currency} /> : fact.value}</dd></div>)}</dl></section>; })}</section></div>
    {tree && <div className="dashboard-card"><BarChart nodes={tree.tree.children ?? []} /></div>}
    <div className="dashboard-card"><Ledger key={`${id}-${bucket}`} id={id} bucket={bucket} /></div>
  </div>;
}
