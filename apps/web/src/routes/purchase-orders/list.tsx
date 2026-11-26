import { createColumnHelper } from "@tanstack/react-table";
import { useCallback, useEffect, useMemo, useState } from "react";
import { apiClient } from "../../api/client";
import DataGrid, { type GridPage } from "../../components/grid/DataGrid";
import { GRID_FEATURES } from "../../components/grid/config";
import StatusPill from "../../components/status/StatusPill";
import { TABS, sentSignal, type PoCounts, type PoRow, type Tab } from "../../components/purchasing/api";
import Money from "../../components/ui/Money";
import Signal from "../../components/ui/Signal";
import { failure, type Problem } from "../../components/requisitions/api";
import type { GridQuery } from "../../lib/grid-params";
import "../../styles/projects.css";
import "../../styles/requisitions.css";
import "../../styles/purchase-orders.css";

const helper = createColumnHelper<typeof GRID_FEATURES, PoRow>();

function read(): Tab {
  const tab = new URLSearchParams(window.location.search).get("tab");
  return (TABS.some(t => t[0] === tab) ? tab : "all") as Tab;
}

export default function PurchaseOrderList({ navigate }: { navigate: (href: string) => void }) {
  const [tab, setTab] = useState(read);
  const [identity, setIdentity] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [retry, setRetry] = useState(0);
  const [counts, setCounts] = useState<PoCounts | null>(null);
  useEffect(() => {
    let active = true;
    void apiClient.GET("/api/v1/auth/me").then(({ data, error }) => {
      if (!active) return;
      if (data) { setIdentity(data.identity_id); setError(null); } else setError(failure(error as Problem | undefined, "Loading your saved views"));
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
  const open = useCallback((e: React.MouseEvent, id: string) => { e.preventDefault(); navigate(`/purchase-orders/${id}`); }, [navigate]);
  const columns = useMemo(() => helper.columns([
    helper.accessor("number", { header: "PO Number", meta: { label: "PO Number" }, enableSorting: false, cell: ({ row }) => <a className="numeric" href={`/purchase-orders/${row.original.id}`} onClick={e => open(e, row.original.id)}>{row.original.number}</a> }),
    helper.accessor("vendor_name", { header: "Vendor", meta: { label: "Vendor" }, enableSorting: false }),
    helper.display({ id: "project", header: "Project", meta: { label: "Project" }, cell: ({ row }) => {
      const { projects, project_count } = row.original;
      if (project_count === 0) return "—";
      if (project_count === 1) return <span title={projects[0]!.name}>{projects[0]!.code}</span>;
      const list = projects.map(p => `${p.code} ${p.name}`).join("; ") + (project_count > projects.length ? `; and ${project_count - projects.length} more` : "");
      return <span tabIndex={0} title={list} aria-label={`${project_count} projects: ${list}`}>{project_count} projects</span>;
    } }),
    helper.accessor("status", { header: "Status", meta: { label: "Status" }, enableSorting: false, cell: ({ getValue }) => <StatusPill docType="purchase_order" status={getValue() as "draft"} /> }),
    helper.display({ id: "version", header: "Version", meta: { label: "Version" }, cell: ({ row }) => <span className="numeric">v{row.original.revision + 1}</span> }),
    helper.display({ id: "sent", header: "Sent", meta: { label: "Sent" }, cell: ({ row }) => { const s = row.original.sent; if (!s) return "—"; const { tone, label } = sentSignal(s); return <Signal tone={tone} label={label} />; } }),
    helper.accessor("total", { header: "Total", meta: { label: "Total" }, enableSorting: false, cell: ({ row }) => <span className="po-total"><Money zero="value" value={row.original.total} currency={row.original.currency} /></span> }),
  ]), [open]);
  const loadPage = useCallback(async (query: GridQuery, signal: AbortSignal): Promise<GridPage<PoRow>> => {
    const q = query.filter?.trim();
    const { data, error } = await apiClient.GET("/api/v1/purchase-orders", { signal, params: { query: { tab, ...(q ? { q } : {}), ...(query.cursor ? { cursor: query.cursor } : {}) } } });
    if (!data) throw new Error(failure(error as Problem | undefined, "Loading purchase orders"));
    setCounts(data.counts);
    // ponytail: no lastCursor, so Ctrl+End stops at the loaded rows; a search reports the rows loaded as its total (counts are per tab, not per search).
    return { rows: data.rows, total: q ? data.rows.length : (data.counts[tab] ?? data.rows.length), startIndex: 0, nextCursor: data.next_cursor ?? null, previousCursor: null, lastCursor: null };
  }, [tab]);
  return <section className="req-page req-list" aria-label="Purchase order list">
    <header className="req-heading"><p>{counts ? `${counts.all} ${counts.all === 1 ? "purchase order" : "purchase orders"}` : ""}</p>{/* New PO is the one Primary; it arrives with the PO editor (E13-S13) */}</header>
    <div role="tablist" aria-label="Purchase order views" className="req-tabs">{TABS.map(([name, text], i) => <button key={name} type="button" role="tab" id={`po-tab-${name}`} aria-selected={tab === name} aria-controls="po-panel" tabIndex={tab === name ? 0 : -1} onClick={() => change(name)} onKeyDown={e => {
      const next = e.key === "ArrowRight" ? TABS[(i + 1) % TABS.length] : e.key === "ArrowLeft" ? TABS[(i + TABS.length - 1) % TABS.length] : e.key === "Home" ? TABS[0] : e.key === "End" ? TABS[TABS.length - 1] : undefined;
      if (next) { e.preventDefault(); change(next[0]); document.getElementById(`po-tab-${next[0]}`)?.focus(); }
    }}>{text}{counts && <span className="req-tab-count"> {counts[name]}</span>}</button>)}</div>
    {error && <div role="alert"><p>{error}</p><button type="button" onClick={() => setRetry(n => n + 1)}>Try again</button></div>}
    {!identity && !error && <div className="req-card req-card-skeleton" aria-busy="true"><p className="req-skeleton" role="status" aria-label="Loading purchase orders" /></div>}
    {identity && <div role="tabpanel" id="po-panel" aria-labelledby={`po-tab-${tab}`}><DataGrid key={tab} ariaLabel="Purchase orders data grid" columns={columns} endpoint="/api/v1/purchase-orders" loadPage={loadPage}
      emptyActionLabel="Show all purchase orders" emptyMessage="No purchase orders here. Orders in this view appear here." filterLabel="Filter orders" filterPlaceholder="Number, vendor or project"
      getRowId={row => row.id} gridId={`purchase-orders-${tab}`} onEmptyAction={() => change("all")} onOpenRow={row => navigate(`/purchase-orders/${row.id}`)}
      recordLabel="purchase orders" sortableColumnIds={[]} userId={identity} /></div>}
  </section>;
}
