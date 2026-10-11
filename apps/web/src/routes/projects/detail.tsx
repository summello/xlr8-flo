import { useCallback, useEffect, useState } from "react";
import { apiClient } from "../../api/client";
import ProjectLoading from "../../components/projects/Loading";
import StatusPill from "../../components/status/StatusPill";
import Transitions from "../../components/projects/Transitions";
import Schedule from "../../components/projects/Schedule";
import Risks from "../../components/projects/Risks";
import Budget from "../../components/projects/Budget";
import { failure, label, type Project, type Transition } from "../../components/projects/api";
import "../../styles/projects.css";
const tabs = ["overview", "schedule", "risks", "budget"] as const;
type Tab = typeof tabs[number];
function currentTab(): Tab {
  const value = new URLSearchParams(window.location.search).get("tab");
  return tabs.includes(value as Tab) ? value as Tab : "overview";
}
export default function ProjectDetail({ id, onLoaded }: { id: string; onLoaded: (project: Project, unit: string) => void }) {
  const [project, setProject] = useState<Project | null>(null);
  const [options, setOptions] = useState<Transition[]>([]);
  const [tab, setTab] = useState<Tab>(currentTab);
  const [visited, setVisited] = useState<Set<Tab>>(() => new Set([currentTab()]));
  const [errors, setErrors] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const load = useCallback(async () => {
    const results = await Promise.allSettled([
      apiClient.GET("/api/v1/projects/{id}", { params: { path: { id } } }),
      apiClient.GET("/api/v1/projects/{id}/transitions/available", { params: { path: { id } } }),
    ]);
    const problems: string[] = [];
    const record = results[0]; const transitions = results[1];
    if (record.status === "fulfilled" && record.value.data) {
      const data = record.value.data; setProject(data);
      onLoaded(data, data.bu_name ?? data.bu_id);
    } else problems.push(failure(record.status === "fulfilled" ? record.value.error : undefined, "Loading project"));
    if (transitions.status === "fulfilled" && transitions.value.data) setOptions(transitions.value.data);
    else problems.push(failure(transitions.status === "fulfilled" ? transitions.value.error : undefined, "Loading transitions"));
    setErrors(problems); setLoading(false);
  }, [id, onLoaded]);
  // load updates state only after network responses; this effect starts the request.
  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    const pop = () => { const next = currentTab(); setTab(next); setVisited(v => new Set([...v, next])); };
    window.addEventListener("popstate", pop);
    return () => window.removeEventListener("popstate", pop);
  }, []);
  const activate = (next: Tab) => {
    const params = new URLSearchParams(window.location.search); params.set("tab", next);
    window.history.pushState(null, "", `${window.location.pathname}?${params}`);
    setTab(next); setVisited(v => new Set([...v, next]));
  };
  return <section className="projects project-detail">
    {loading && !project && tab === "overview" ? <ProjectLoading /> : loading && <p role="status">Loading project… Previously loaded details are preserved.</p>}
    {errors.length > 0 && <div role="alert">{errors.map(e => <p key={e}>{e}</p>)}<button onClick={() => { setLoading(true); void load(); }}>Retry project</button></div>}
    {project && <>
      <header className="project-record-heading"><p className="numeric">{project.number}</p><StatusPill docType="project" status={project.status} /></header>
      <div role="tablist" aria-label="Project sections" className="project-tabs">{tabs.map((name, index) => <button key={name} type="button" id={`tab-${name}`} role="tab" aria-controls={`panel-${name}`} aria-selected={tab === name} tabIndex={tab === name ? 0 : -1} onClick={() => activate(name)} onKeyDown={e => {
        const next = e.key === "ArrowRight" ? tabs[(index + 1) % tabs.length] : e.key === "ArrowLeft" ? tabs[(index + tabs.length - 1) % tabs.length] : e.key === "Home" ? tabs[0] : e.key === "End" ? tabs[tabs.length - 1] : undefined;
        if (next) { e.preventDefault(); activate(next); document.getElementById(`tab-${next}`)?.focus(); }
      }}>{label(name)}</button>)}</div>
      {tabs.map(name => <div key={name} id={`panel-${name}`} role="tabpanel" aria-labelledby={`tab-${name}`} hidden={tab !== name} tabIndex={0}>
        {visited.has(name) && (name === "overview" ? <>
          <h2>Overview</h2><dl className="project-facts">{(["name", "description", "owner_id", "sponsor_id", "department_code", "ledger_account_code", "currency", "health", "percent_complete", "planned_start", "planned_end", "actual_start", "actual_end", "schedule_variance_days"] as const).map(key => <div key={key}><dt>{label(key)}</dt><dd>{project[key] === null ? "—" : String(project[key])}</dd></div>)}</dl>
          <h2>Available actions</h2><Transitions project={project} options={options} reload={() => { setLoading(true); void load(); }} onUpdated={data => { setProject(data); setLoading(true); void load(); }} />
        </> : name === "schedule" ? <><h2>Schedule</h2><Schedule id={id} /></> : name === "risks" ? <><h2>Risks</h2><Risks id={id} owner={project.owner_id} /></> : <><h2>Budget</h2><Budget id={id} /></>)}
      </div>)}
    </>}
  </section>;
}
