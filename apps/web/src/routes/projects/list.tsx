import { createColumnHelper } from "@tanstack/react-table";
import { useCallback, useEffect, useMemo, useState } from "react";
import { apiClient } from "../../api/client";
import DataGrid, { type GridPage } from "../../components/grid/DataGrid";
import { GRID_FEATURES } from "../../components/grid/config";
import StatusPill from "../../components/status/StatusPill";
import ProjectPreview from "../../components/projects/Preview";
import Money from "../../components/ui/Money";
import { failure, label, statuses, type Project, type ProjectStatus } from "../../components/projects/api";
import type { GridQuery } from "../../lib/grid-params";
import type { GridViewDefinition } from "../../lib/grid-preferences";
import "../../styles/projects.css";

type Extra = NonNullable<GridViewDefinition["extra"]>;
const helper = createColumnHelper<typeof GRID_FEATURES, Project>();
const sorts = ["number", "name", "status", "created_at"] as const;
function readExtra(): Extra {
  const params = new URLSearchParams(window.location.search);
  const group = params.get("group_by");
  const status = params.get("status");
  return {
    ...(group === "status" || group === "bu" ? { group_by: group } : {}),
    ...(status && statuses.includes(status as ProjectStatus) ? { status } : {}),
    ...(params.get("bu_id") ? { bu_id: params.get("bu_id")! } : {}),
  };
}

export default function ProjectList({ navigate }: { navigate: (href: string) => void }) {
  const [preview, setPreview] = useState<Project | null>(null);
  const [origin, setOrigin] = useState<HTMLElement | null>(null);
  const [extra, setExtra] = useState<Extra>(readExtra);
  const [identity, setIdentity] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [retry, setRetry] = useState(0);
  const [units, setUnits] = useState<{ label: string; value: string }[]>([]);
  useEffect(() => {
    let active = true;
    void apiClient.GET("/api/v1/auth/me").then(({ data, error }) => {
      if (!active) return;
      if (data) { setIdentity(data.identity_id); setError(null); }
      else setError(failure(error, "Loading your saved views"));
    }).catch(() => { if (active) setError(failure(undefined, "Loading your saved views")); });
    void apiClient.GET("/api/v1/org/units", { params: { query: { page_size: 50 } } }).then(({ data }) => {
      if (active && data) setUnits(data.rows.map(u => ({ label: u.name, value: u.id })));
    }).catch(() => { /* The list stays usable with the existing BU filter. */ });
    return () => { active = false; };
  }, [retry]);
  useEffect(() => {
    const pop = () => setExtra(current => {
      const next = readExtra();
      return current.group_by === next.group_by && current.status === next.status && current.bu_id === next.bu_id ? current : next;
    });
    window.addEventListener("popstate", pop);
    return () => window.removeEventListener("popstate", pop);
  }, []);
  const changeExtra = useCallback((next: GridViewDefinition["extra"]) => {
    const value = next ?? {};
    const params = new URLSearchParams(window.location.search);
    for (const key of ["group_by", "status", "bu_id"] as const) {
      if (value[key]) params.set(key, value[key]); else params.delete(key);
    }
    params.delete("cursor");
    window.history.pushState(null, "", `${window.location.pathname}?${params}`);
    setExtra(value);
  }, []);
  const columns = useMemo(() => {
    const groupKey = (row: Project) => extra.group_by === "status" ? row.status : row.bu_id;
    const grouped = (row: Project) => extra.group_by === "status" ? label(row.status) : row.bu_name ?? row.bu_id;
    return helper.columns([
      ...(extra.group_by ? [helper.display({
        id: "group", header: "Group", meta: { label: "Group" }, enableSorting: false,
        cell: ({ row, table }) => {
          const previous = table.getRowModel().rows[row.index - 1]?.original;
          const first = !previous || groupKey(previous) !== groupKey(row.original);
          return first ? <span className="project-group-start"><span className="visually-hidden">Group: </span>{grouped(row.original)}</span> : null;
        },
      })] : []),
      helper.accessor("number", { header: "Number", meta: { label: "Number" }, enableSorting: !extra.group_by }),
      helper.accessor("name", { header: "Name", meta: { label: "Name" }, enableSorting: !extra.group_by,
        cell: ({ row }) => <a href={`/projects/${row.original.id}`} onClick={e => { e.preventDefault(); navigate(`/projects/${row.original.id}`); }}>{row.original.name}</a> }),
      helper.accessor("status", { header: "Status", meta: { label: "Status" }, enableSorting: !extra.group_by,
        cell: ({ getValue }) => <StatusPill docType="project" status={getValue()} /> }),
      helper.accessor("bu_name", { header: "BU / OU", meta: { label: "BU / OU" }, enableSorting: false }),
      helper.accessor("owner_id", { header: "Owner", meta: { label: "Owner" }, enableSorting: false }),
      helper.accessor("health", { header: "Health", meta: { label: "Health" }, enableSorting: false, cell: ({ getValue }) => label(getValue()) }),
      helper.accessor("allocated", { header: "Allocated", meta: { label: "Allocated", numeric: true }, enableSorting: false,
        cell: ({ row }) => <Money value={row.original.allocated ?? null} currency={row.original.currency} /> }),
      helper.accessor("available", { header: "Available", meta: { label: "Available", numeric: true }, enableSorting: false,
        cell: ({ row }) => <Money value={row.original.available ?? null} currency={row.original.currency} /> }),
    ]);
  }, [extra.group_by, navigate]);
  const loadPage = useCallback(async (query: GridQuery, signal: AbortSignal): Promise<GridPage<Project>> => {
    const { data, error } = await apiClient.GET("/api/v1/projects", { signal, params: { query: {
      q: query.filter, sort: query.sort?.id as typeof sorts[number] | undefined,
      direction: query.sort?.direction, cursor: query.cursor ?? undefined, page_size: 50,
      status: extra.status as ProjectStatus | undefined, bu_id: extra.bu_id, group_by: extra.group_by,
    } } });
    if (!data) throw new Error(failure(error, "Loading projects"));
    // ponytail: forward-only cursors cannot prepend or jump to last; add reverse cursors when needed.
    return { rows: data.rows, total: data.total, startIndex: 0, nextCursor: data.next_cursor, previousCursor: null, lastCursor: null };
  }, [extra]);
  return <section className="projects">
    <div className="project-toolbar"><a className="form-submit" href="/projects/new" onClick={e => { e.preventDefault(); navigate("/projects/new"); }}>Create project</a>
      <label>Group by<select aria-label="Group by" value={extra.group_by ?? ""} onChange={e => changeExtra({ ...extra, group_by: e.target.value as Extra["group_by"] || undefined })}><option value="">None</option><option value="status">Status</option><option value="bu">Business unit</option></select></label>
      <label>Status filter<select aria-label="Status filter" value={extra.status ?? ""} onChange={e => changeExtra({ ...extra, status: e.target.value || undefined })}><option value="">All statuses</option>{statuses.map(s => <option key={s} value={s}>{label(s)}</option>)}</select></label>
      <label>Business unit filter<select aria-label="Business unit filter" value={extra.bu_id ?? ""} onChange={e => changeExtra({ ...extra, bu_id: e.target.value || undefined })}><option value="">All units</option>{units.map(u => <option key={u.value} value={u.value}>{u.label}</option>)}</select></label>
    </div>
    <p aria-live="polite">{extra.group_by ? "Sorting is disabled while grouped. Projects are ordered by group, number and id." : "Search, sort and save a personal project view."}</p>
    {error && <div role="alert"><p>{error}</p><button onClick={() => setRetry(n => n + 1)}>Try again</button></div>}
    {!identity && !error && <p role="status">Loading your project views…</p>}
    {identity && <DataGrid ariaLabel="Projects data grid" columns={columns} endpoint="/api/v1/projects" loadPage={loadPage}
      emptyActionLabel="Add your first project — press C" emptyMessage="No projects match this view." filterLabel="Search projects" filterPlaceholder="Search number or name"
      getRowId={row => row.id} gridId="projects" onEmptyAction={() => navigate("/projects/new")} onOpenRow={(row, element) => { setOrigin(element); setPreview(row); }}
      recordLabel="projects" sortableColumnIds={sorts} userId={identity} viewExtra={extra} onViewExtra={changeExtra} />}
    <ProjectPreview project={preview} origin={origin} close={() => setPreview(null)} open={id => navigate(`/projects/${id}`)} />
  </section>;
}
