import { Plus } from "@phosphor-icons/react";
import { createColumnHelper } from "@tanstack/react-table";
import { useCallback, useEffect, useMemo, useState } from "react";
import { apiClient } from "../../../api/client";
import DataGrid, { type GridPage } from "../../../components/grid/DataGrid";
import { GRID_FEATURES } from "../../../components/grid/config";
import StatusPill from "../../../components/status/StatusPill";
import { MAX_INVITEES, relativeDeadline, type RfqCounts, type RfqListRow } from "../../../components/sourcing/api";
import Person from "../../../components/ui/Person";
import { failure, formatDate } from "../../../components/requisitions/api";
import type { GridQuery } from "../../../lib/grid-params";
import "../../../styles/projects.css";
import "../../../styles/requisitions.css";
import "../../../styles/sourcing.css";

const TABS = [["drafts", "Drafts"], ["open", "Open"], ["evaluating", "Evaluating"], ["awarded", "Awarded"], ["all", "All"]] as const;
type Tab = (typeof TABS)[number][0];
const PAGE = 50; // the shared grid's page size (GRID_PAGE_SIZE) and the server's cap
const helper = createColumnHelper<typeof GRID_FEATURES, RfqListRow>();

function read(): Tab {
  const tab = new URLSearchParams(window.location.search).get("tab");
  return (TABS.some(t => t[0] === tab) ? tab : "all") as Tab;
}

export default function RfqList({ navigate }: { navigate: (href: string) => void }) {
  const [tab, setTab] = useState(read);
  const [identity, setIdentity] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [retry, setRetry] = useState(0);
  const [counts, setCounts] = useState<RfqCounts | null>(null);
  useEffect(() => {
    let active = true;
    void apiClient.GET("/api/v1/auth/me").then(({ data, error }) => {
      if (!active) return;
      if (data) { setIdentity(data.identity_id); setError(null); } else setError(failure(error, "Loading your saved views"));
    }).catch(() => { if (active) setError(failure(undefined, "Loading your saved views")); });
    return () => { active = false; };
  }, [retry]);
  useEffect(() => {
    const pop = () => setTab(read());
    window.addEventListener("popstate", pop);
    return () => window.removeEventListener("popstate", pop);
  }, []);
  const change = (next: Tab) => {
    window.history.pushState(null, "", next === "all" ? window.location.pathname : `${window.location.pathname}?tab=${next}`);
    setTab(next);
  };
  const columns = useMemo(() => helper.columns([
    helper.accessor("number", { header: "Number", meta: { label: "Number" }, enableSorting: false, cell: ({ row }) => <a className="numeric" href={`/sourcing/rfqs/${row.original.id}`} onClick={e => { e.preventDefault(); navigate(`/sourcing/rfqs/${row.original.id}`); }}>{row.original.number}</a> }),
    helper.accessor("title", { header: "Title", meta: { label: "Title" }, enableSorting: false, cell: ({ row }) => <span className="req-title-cell"><span>{row.original.title}</span><span className="req-sub">{row.original.requisition_numbers.join(", ")}</span></span> }),
    helper.accessor("status", { header: "Status", meta: { label: "Status" }, enableSorting: false, cell: ({ getValue }) => <StatusPill docType="rfq" status={getValue()} /> }),
    helper.accessor("invitee_count", { header: "Invited", meta: { label: "Invited" }, enableSorting: false, cell: ({ getValue }) => `${getValue()} of ${MAX_INVITEES}` }),
    helper.accessor("deadline_at", { header: "Deadline", meta: { label: "Deadline" }, enableSorting: false, cell: ({ getValue }) => <span>{formatDate(getValue())}<span className="req-sub">{relativeDeadline(getValue())}</span></span> }),
    helper.accessor("owner_email", { header: "Owner", meta: { label: "Owner" }, enableSorting: false, cell: ({ row }) => <Person name={row.original.owner_name} email={row.original.owner_email} /> }),
  ]), [navigate]);
  const loadPage = useCallback(async (query: GridQuery, signal: AbortSignal): Promise<GridPage<RfqListRow>> => {
    const { data, error } = await apiClient.GET("/api/v1/sourcing/rfqs", { signal, params: { query: {
      tab, q: query.filter, page_size: PAGE, ...(query.cursor ? { cursor: query.cursor } : {}),
    } } });
    if (!data) throw new Error(failure(error, "Loading RFQs"));
    setCounts(data.counts);
    // The grid owns paging: it appends the next page when nextCursor is set. ponytail: no lastCursor, so Ctrl+End stops at the loaded rows.
    return { rows: data.rows, total: data.counts[tab], startIndex: 0, nextCursor: data.next_cursor ?? null, previousCursor: null, lastCursor: null };
  }, [tab]);
  return <section className="req-page req-list" aria-label="RFQ list">
    <header className="req-heading"><p>{counts ? `${counts.all} ${counts.all === 1 ? "RFQ" : "RFQs"}` : ""}</p>
      <a className="req-link-button form-submit" href="/sourcing/rfqs/new" onClick={e => { e.preventDefault(); navigate("/sourcing/rfqs/new"); }}><Plus aria-hidden="true" weight="regular" />New RFQ</a></header>
    <div role="tablist" aria-label="RFQ views" className="req-tabs">{TABS.map(([name, text], i) => <button key={name} type="button" role="tab" id={`rfq-tab-${name}`} aria-selected={tab === name} aria-controls="rfq-panel" tabIndex={tab === name ? 0 : -1} onClick={() => change(name)} onKeyDown={e => {
      const next = e.key === "ArrowRight" ? TABS[(i + 1) % TABS.length] : e.key === "ArrowLeft" ? TABS[(i + TABS.length - 1) % TABS.length] : e.key === "Home" ? TABS[0] : e.key === "End" ? TABS[TABS.length - 1] : undefined;
      if (next) { e.preventDefault(); change(next[0]); document.getElementById(`rfq-tab-${next[0]}`)?.focus(); }
    }}>{text}{counts && <span className="req-tab-count"> {counts[name]}</span>}</button>)}</div>
    {error && <div role="alert"><p>{error}</p><button type="button" onClick={() => setRetry(n => n + 1)}>Try again</button></div>}
    {!identity && !error && <div className="req-card req-card-skeleton" aria-busy="true"><p className="req-skeleton" role="status" aria-label="Loading RFQs" /></div>}
    {identity && <div role="tabpanel" id="rfq-panel" aria-labelledby={`rfq-tab-${tab}`}><DataGrid key={tab} ariaLabel="RFQs data grid" columns={columns} endpoint="/api/v1/sourcing/rfqs" loadPage={loadPage}
      emptyActionLabel="Show all RFQs" emptyMessage="No RFQs here. RFQs in this view appear here." filterLabel="Search RFQs" filterPlaceholder="Search number or title"
      getRowId={row => row.id} gridId={`rfqs-${tab}`} onEmptyAction={() => change("all")} onOpenRow={row => navigate(`/sourcing/rfqs/${row.id}`)}
      recordLabel="RFQs" sortableColumnIds={[]} userId={identity} /></div>}
  </section>;
}
