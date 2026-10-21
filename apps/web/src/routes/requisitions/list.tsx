import { createColumnHelper } from "@tanstack/react-table";
import { Plus } from "@phosphor-icons/react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { apiClient } from "../../api/client";
import DataGrid, { type GridPage } from "../../components/grid/DataGrid";
import { GRID_FEATURES } from "../../components/grid/config";
import StatusPill from "../../components/status/StatusPill";
import Money from "../../components/requisitions/ReqMoney";
import { failure, formatDate, label, statuses, type LookupOption, type Requisition, type RequisitionStatus } from "../../components/requisitions/api";
import type { GridQuery } from "../../lib/grid-params";
import "../../styles/projects.css";
import "../../styles/requisitions.css";

const TABS = [["mine", "Mine"], ["drafts", "Drafts"], ["submitted", "Submitted"], ["requests", "Project Requests"], ["all", "All"]] as const;
type Tab = typeof TABS[number][0];
const sorts = ["number", "title", "estimate", "needed_by", "updated_at"] as const;
const PAGE = 10;
type PageInfo = { total: number; rows: number; next: string | null; totals: { currency: string; amount: string }[] };
const helper = createColumnHelper<typeof GRID_FEATURES, Requisition>();

function read(): { tab: Tab; status: RequisitionStatus | ""; project: string } {
  const params = new URLSearchParams(window.location.search);
  const tab = params.get("tab");
  const status = params.get("status");
  return {
    tab: (TABS.some(t => t[0] === tab) ? tab : "mine") as Tab,
    status: statuses.includes(status as RequisitionStatus) ? status as RequisitionStatus : "",
    project: params.get("project_id") ?? "",
  };
}
/** The tab fixes the requester/status filter; a Status filter only narrows "Mine" and "All". */
function tabQuery(tab: Tab) {
  return tab === "mine" ? { requester: "me" as const } : tab === "drafts" ? { requester: "me" as const, status: "draft" as const }
    : tab === "submitted" ? { requester: "me" as const, status: "approval_pending" as const }
    : tab === "requests" ? { kind: "project_request" as const } : {};
}

export default function RequisitionList({ navigate }: { navigate: (href: string) => void }) {
  const [filters, setFilters] = useState(read);
  const [identity, setIdentity] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [retry, setRetry] = useState(0);
  const [counts, setCounts] = useState<Partial<Record<Tab, number>>>({});
  const [projects, setProjects] = useState<LookupOption[]>([]);
  // Cursor-backed pager: pages[i] is the cursor that fetches page i; the grid only ever holds the current page.
  const [pages, setPages] = useState<(string | null)[]>([null]);
  const [index, setIndex] = useState(0);
  const [info, setInfo] = useState<PageInfo | null>(null);
  const order = useRef("");
  const cursor = pages[index] ?? null;
  useEffect(() => {
    let active = true;
    void apiClient.GET("/api/v1/auth/me").then(({ data, error }) => {
      if (!active) return;
      if (data) { setIdentity(data.identity_id); setError(null); } else setError(failure(error, "Loading your saved views"));
    }).catch(() => { if (active) setError(failure(undefined, "Loading your saved views")); });
    void apiClient.GET("/api/v1/requisitions/lookups/{kind}", { params: { path: { kind: "project" }, query: { limit: 50 } } })
      .then(({ data }) => { if (active && data) setProjects(data.rows); }).catch(() => { /* the filter simply stays empty */ });
    return () => { active = false; };
  }, [retry]);
  useEffect(() => {
    let active = true;
    for (const [tab] of TABS) {
      void apiClient.GET("/api/v1/requisitions", { params: { query: tabQuery(tab) } })
        .then(({ data }) => { if (active && data) setCounts(c => ({ ...c, [tab]: data.total })); }).catch(() => { /* count stays hidden */ });
    }
    return () => { active = false; };
  }, [retry]);
  useEffect(() => {
    const pop = () => { setFilters(read()); setPages([null]); setIndex(0); };
    window.addEventListener("popstate", pop);
    return () => window.removeEventListener("popstate", pop);
  }, []);
  const change = (next: Partial<typeof filters>) => {
    setPages([null]); setIndex(0);
    const value = { ...filters, ...next };
    const params = new URLSearchParams();
    if (value.tab !== "mine") params.set("tab", value.tab);
    if (value.status) params.set("status", value.status);
    if (value.project) params.set("project_id", value.project);
    window.history.pushState(null, "", `${window.location.pathname}${params.size ? `?${params}` : ""}`);
    setFilters(value);
  };
  const columns = useMemo(() => helper.columns([
    helper.accessor("number", { header: "Number", meta: { label: "Number" }, cell: ({ row }) => <a className="numeric" href={`/requisitions/${row.original.id}`} onClick={e => { e.preventDefault(); navigate(`/requisitions/${row.original.id}`); }}>{row.original.number}</a> }),
    helper.accessor("title", { header: "Title", meta: { label: "Title" }, cell: ({ row }) => <span className="req-title-cell"><span>{row.original.title}</span><span className="req-sub">{row.original.requester_email}</span></span> }),
    helper.accessor("project_name", { header: "Project", meta: { label: "Project" }, enableSorting: false, cell: ({ row }) => row.original.project_name ? `${row.original.project_number} ${row.original.project_name}` : "Project Request" }),
    helper.accessor("estimate", { header: "Amount", meta: { label: "Amount", numeric: true }, cell: ({ row }) => <Money value={row.original.estimate} currency={row.original.currency} /> }),
    helper.accessor("status", { header: "Status", meta: { label: "Status" }, enableSorting: false, cell: ({ getValue }) => <StatusPill docType="requisition" status={getValue() as RequisitionStatus} /> }),
    helper.accessor("needed_by", { header: "Needed by", meta: { label: "Needed by" }, cell: ({ getValue }) => formatDate(getValue()) }),
    helper.accessor("updated_at", { header: "Updated", meta: { label: "Updated" }, cell: ({ getValue }) => formatDate(getValue()) }),
  ]), [navigate]);
  const { tab, status, project } = filters;
  const loadPage = useCallback(async (query: GridQuery, signal: AbortSignal): Promise<GridPage<Requisition>> => {
    const fixed: { requester?: "me"; status?: RequisitionStatus; kind?: "project_request" } = tabQuery(tab);
    // A cursor belongs to one sort and search; when either changes, start again from page 1.
    const key = JSON.stringify([query.sort, query.filter]);
    const sameOrder = order.current === "" || order.current === key;
    order.current = key;
    if (!sameOrder && index !== 0) { setPages([null]); setIndex(0); }
    const { data, error } = await apiClient.GET("/api/v1/requisitions", { signal, params: { query: {
      ...fixed, status: fixed.status ?? (status || undefined), ...(project ? { project_id: project } : {}), page_size: PAGE,
      q: query.filter, sort: query.sort?.id as typeof sorts[number] | undefined, direction: query.sort?.direction, cursor: sameOrder ? cursor ?? undefined : undefined,
    } } });
    if (!data) throw new Error(failure(error, "Loading requisitions"));
    setInfo({ total: data.total, rows: data.rows.length, next: data.next_cursor ?? null, totals: data.totals ?? [] });
    return { rows: data.rows, total: data.total, startIndex: 0, nextCursor: null, previousCursor: null, lastCursor: null };
  }, [tab, status, project, cursor, index, setPages, setIndex]);
  const filtered = Boolean(status || project);
  return <section className="req-page req-list" aria-label="Requisition list">
    <header className="req-heading">
      <p>{counts.all === undefined ? "" : `${counts.all} ${counts.all === 1 ? "requisition" : "requisitions"} · amounts in each requisition's currency`}</p>
      <a className="form-submit" href="/requisitions/new" onClick={e => { e.preventDefault(); navigate("/requisitions/new"); }}><Plus aria-hidden="true" weight="regular" />New requisition</a>
    </header>
    <div role="tablist" aria-label="Requisition views" className="req-tabs">{TABS.map(([name, text], index) => <button key={name} type="button" role="tab" id={`req-tab-${name}`} aria-selected={tab === name} tabIndex={tab === name ? 0 : -1} onClick={() => change({ tab: name })} onKeyDown={e => {
      const next = e.key === "ArrowRight" ? TABS[(index + 1) % TABS.length] : e.key === "ArrowLeft" ? TABS[(index + TABS.length - 1) % TABS.length] : e.key === "Home" ? TABS[0] : e.key === "End" ? TABS[TABS.length - 1] : undefined;
      if (next) { e.preventDefault(); change({ tab: next[0] }); document.getElementById(`req-tab-${next[0]}`)?.focus(); }
    }}>{text}{counts[name] !== undefined && <span className="req-tab-count"> {counts[name]}</span>}</button>)}</div>
    <div role="search" className="req-filters">
      <label>Status<select aria-label="Status filter" value={status} disabled={tab === "drafts" || tab === "submitted"} onChange={e => change({ status: e.target.value as RequisitionStatus | "" })}><option value="">All statuses</option>{statuses.map(s => <option key={s} value={s}>{label(s)}</option>)}</select></label>
      <label>Project<select aria-label="Project filter" value={project} onChange={e => change({ project: e.target.value })}><option value="">All projects</option>{projects.map(p => <option key={p.value} value={p.value}>{p.label}</option>)}</select></label>
      {filtered && <button type="button" onClick={() => change({ status: "", project: "" })}>Clear filters</button>}
    </div>
    {error && <div role="alert"><p>{error}</p><button type="button" onClick={() => setRetry(n => n + 1)}>Try again</button></div>}
    {!identity && !error && <div className="req-card req-card-skeleton" aria-busy="true"><p className="req-skeleton" role="status" aria-label="Loading requisitions" /></div>}
    {identity && <div role="tabpanel" aria-labelledby={`req-tab-${tab}`}><DataGrid key={`${tab}|${status}|${project}`} ariaLabel="Requisitions data grid" columns={columns} endpoint="/api/v1/requisitions" loadPage={loadPage}
      emptyActionLabel="Create your first requisition" emptyMessage={filtered ? "No Requisitions Match. Clear filters to see everything in this view." : "No Requisitions Yet"} filterLabel="Search requisitions" filterPlaceholder="Search number, title or project"
      getRowId={row => row.id} gridId={`requisitions-${tab}`} onEmptyAction={() => navigate("/requisitions/new")} onOpenRow={row => navigate(`/requisitions/${row.id}`)}
      recordLabel="requisitions" sortableColumnIds={sorts} userId={identity} />
      {info && <div className="req-pager">
        {info.totals.length > 0 && <p className="req-total-row"><span>Total for this view</span>{info.totals.map(t => <Money key={t.currency} value={t.amount} currency={t.currency} />)}</p>}
        <p role="status">{info.total === 0 ? "Showing 0 of 0" : `Showing ${index * PAGE + 1} to ${index * PAGE + info.rows} of ${info.total}`}</p>
        <button type="button" disabled={index === 0} onClick={() => setIndex(i => i - 1)}>Previous page</button>
        <button type="button" disabled={info.next === null} onClick={() => { setPages(p => { const next = p.slice(0, index + 1); next[index + 1] = info.next; return next; }); setIndex(i => i + 1); }}>Next page</button>
      </div>}</div>}
  </section>;
}
