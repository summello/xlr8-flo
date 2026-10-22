import { Plus } from "@phosphor-icons/react";
import { useEffect, useState } from "react";
import { apiClient } from "../../api/client";
import StatusPill from "../../components/status/StatusPill";
import Signal from "../../components/ui/Signal";
import Eligibility from "../../components/vendors/Eligibility";
import { AVAILABILITY, docWords, type DocSummary, type VendorRow } from "../../components/vendors/api";
import { failure } from "../../components/requisitions/api";
import { useLoad } from "../../components/requisitions/useLoad";
import { ROUTE_PATHS } from "../route-paths";
import "../../styles/projects.css";
import "../../styles/requisitions.css";
import "../../styles/vendors.css";

const ATTENTION = ["expiring", "expired", "missing", "rejected"] as const;
const TABS = [
  ["active", "Active", { status: ["active"] }],
  ["pending", "Pending Approval", { status: ["pending_approval", "draft"] }],
  ["attention", "Needs Attention", { documents: [...ATTENTION] }],
  ["inactive", "Inactive", { status: ["suspended", "inactive", "rejected"] }],
  ["all", "All", {}],
] as const;
type Tab = (typeof TABS)[number][0];
const PAGE = 25;

function read() {
  const p = new URLSearchParams(window.location.search);
  const tab = p.get("tab");
  const doc = p.get("documents");
  return {
    tab: (TABS.some(t => t[0] === tab) ? tab : "active") as Tab,
    q: p.get("q") ?? "",
    // ponytail: the server attaches eligibility only for a named unit, so "here" is the default view.
    here: p.get("here") !== "0",
    documents: ((ATTENTION as readonly string[]).includes(doc ?? "") ? doc : "") as (typeof ATTENTION)[number] | "",
  };
}

function Documents({ summary }: { summary: DocSummary[] | null | undefined }) {
  if (!summary) return <span>—</span>;
  if (summary.length === 0) return <Signal tone="success" label="Complete" />;
  return <>{summary.map(s => { const w = docWords(s.state, s.days_left, s.count); return <Signal key={s.state} tone={w.tone} label={w.text} />; })}</>;
}

export default function VendorList({ navigate }: { navigate: (href: string) => void }) {
  const [filters, setFilters] = useState(read);
  const [pages, setPages] = useState<(string | null)[]>([null]);
  const [index, setIndex] = useState(0);
  const [draft, setDraft] = useState(filters.q);
  const { tab, q, here, documents } = filters;
  useEffect(() => {
    const pop = () => { const next = read(); setFilters(next); setDraft(next.q); setPages([null]); setIndex(0); };
    window.addEventListener("popstate", pop);
    return () => window.removeEventListener("popstate", pop);
  }, []);
  const change = (next: Partial<typeof filters>) => {
    const value = { ...filters, ...next };
    const params = new URLSearchParams();
    if (value.tab !== "active") params.set("tab", value.tab);
    if (value.q) params.set("q", value.q);
    if (!value.here) params.set("here", "0");
    if (value.documents) params.set("documents", value.documents);
    window.history.pushState(null, "", `${window.location.pathname}${params.size ? `?${params}` : ""}`);
    setFilters(value); setPages([null]); setIndex(0);
  };
  // Eligible Here is worked out by the server for one unit. ponytail: the first unit stands in until the user has a default unit.
  const unit = useLoad(async () => {
    const { data } = await apiClient.GET("/api/v1/org/units", { params: { query: { page_size: 1 } } });
    return data?.rows[0] ?? null;
  }, []);
  const counts = useLoad(async () => (await apiClient.GET("/api/v1/vendors/counts")).data ?? null, []);
  const fixed = TABS.find(t => t[0] === tab)![2] as { status?: readonly VendorRow["status"][]; documents?: readonly (typeof ATTENTION)[number][] };
  const cursor = pages[index] ?? null;
  const unitId = unit.data?.id;
  const list = useLoad(async () => {
    if (here && unit.loading) return null; // wait for the unit so the first request is the right one
    const { data, error } = await apiClient.GET("/api/v1/vendors", { params: { query: {
      q, limit: PAGE, ...(cursor ? { cursor } : {}),
      ...(fixed.status ? { status: [...fixed.status] } : {}),
      ...(documents ? { documents: [documents as (typeof ATTENTION)[number]] } : fixed.documents ? { documents: [...fixed.documents] } : {}),
      ...(here && unitId ? { available_in: unitId } : {}),
    } } });
    if (!data) throw new Error(failure(error, "Loading vendors"));
    return data;
  }, [tab, q, documents, cursor, unitId, here, unit.loading]);
  const rows = list.data?.rows ?? [];
  const canRequest = (ROUTE_PATHS as readonly string[]).includes("/vendors/new");
  const filtered = Boolean(q || !here || documents);
  return <section className="req-page vendor-list" aria-label="Vendor list">
    <header className="req-heading">
      <p>{counts.data ? `${counts.data.all} ${counts.data.all === 1 ? "vendor" : "vendors"}` : ""}</p>
      {canRequest && <a className="form-submit" href="/vendors/new" onClick={e => { e.preventDefault(); navigate("/vendors/new"); }}><Plus aria-hidden="true" weight="regular" />Request Vendor</a>}
    </header>
    <div role="tablist" aria-label="Vendor views" className="req-tabs">{TABS.map(([name, text], i) => <button key={name} type="button" role="tab" id={`vendor-tab-${name}`} aria-selected={tab === name} aria-controls="vendor-panel" tabIndex={tab === name ? 0 : -1} onClick={() => change({ tab: name })} onKeyDown={e => {
      const next = e.key === "ArrowRight" ? TABS[(i + 1) % TABS.length] : e.key === "ArrowLeft" ? TABS[(i + TABS.length - 1) % TABS.length] : e.key === "Home" ? TABS[0] : e.key === "End" ? TABS[TABS.length - 1] : undefined;
      if (next) { e.preventDefault(); change({ tab: next[0] }); document.getElementById(`vendor-tab-${next[0]}`)?.focus(); }
    }}>{text}{counts.data && <span className="req-tab-count"> {counts.data[name === "pending" ? "pending" : name]}</span>}</button>)}</div>
    <form role="search" className="req-filters" onSubmit={e => { e.preventDefault(); change({ q: draft.trim() }); }}>
      <label>Search<input type="search" value={draft} placeholder="Name or number" onChange={e => setDraft(e.target.value)} /></label>
      <label>Availability<select value={here ? "1" : "0"} onChange={e => change({ here: e.target.value === "1" })}><option value="1">Available in {unit.data?.name ?? "my unit"}</option><option value="0">All vendors</option></select></label>
      <label>Documents<select value={documents} onChange={e => change({ documents: e.target.value as typeof documents })}><option value="">Any</option>{ATTENTION.map(a => <option key={a} value={a}>{docWords(a, 0).text.replace("Expiring Today", "Expiring Soon")}</option>)}</select></label>
      <button type="submit">Search</button>
      {filtered && <button type="button" onClick={() => { setDraft(""); change({ q: "", here: true, documents: "" }); }}>Clear filters</button>}
    </form>
    <div role="tabpanel" id="vendor-panel" aria-labelledby={`vendor-tab-${tab}`}>
      {list.error && <div role="alert"><p>{list.error}</p><button type="button" onClick={list.reload}>Try again</button></div>}
      {!list.data && !list.error && <div className="req-card req-card-skeleton" aria-busy="true"><p className="req-skeleton" role="status" aria-label="Loading vendors" /></div>}
      {list.data && (rows.length === 0 ? <div className="req-empty"><h3>{filtered ? "No Vendors Match" : "No Vendors Here"}</h3><p>{filtered ? "Clear filters to see everything in this view." : "Vendors in this view appear here."}</p></div> :
        <table className="vendor-table" aria-busy={list.loading || undefined}>
          <caption className="visually-hidden">Vendors</caption>
          <thead><tr><th scope="col">Vendor</th><th scope="col">Status</th><th scope="col">Eligible Here</th><th scope="col">Availability</th><th scope="col">Documents</th></tr></thead>
          <tbody>{rows.map(v => <tr key={v.id} data-grid-row>
            <td data-label="Vendor"><a href={`/vendors/${v.id}`} onClick={e => { e.preventDefault(); navigate(`/vendors/${v.id}`); }}>{v.trading_name}</a><span className="req-sub">{v.number} · {v.legal_name}</span></td>
            <td data-label="Status"><StatusPill docType="vendor" status={v.status} /></td>
            <td data-label="Eligible Here">{v.eligibility ? <Eligibility eligibility={v.eligibility} variant="cell" /> : "—"}</td>
            <td data-label="Availability">{AVAILABILITY[v.availability]}</td>
            <td data-label="Documents"><Documents summary={v.documents} /></td>
          </tr>)}</tbody>
        </table>)}
      <div className="req-pager">
        <p role="status">{list.data ? `Showing ${rows.length === 0 ? 0 : index * PAGE + 1} to ${index * PAGE + rows.length}` : ""}</p>
        <button type="button" disabled={index === 0} onClick={() => setIndex(i => i - 1)}>Previous page</button>
        <button type="button" disabled={!list.data?.next_cursor} onClick={() => { const next = list.data?.next_cursor ?? null; setPages(p => [...p.slice(0, index + 1), next]); setIndex(i => i + 1); }}>Next page</button>
      </div>
    </div>
  </section>;
}
