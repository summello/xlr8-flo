import { useEffect, useRef, useState } from "react";
import { Plus, MagnifyingGlass, Check } from "@phosphor-icons/react";
import { apiClient } from "../../../api/client";
import type { components } from "../../../api/generated/schema";
import { writeHeaders } from "../../../features/auth/api";
import History from "../../../components/workflows/history/History";
import Sheet, { Pill } from "../../../components/workflows/history/Sheet";
import Field from "../../../components/form/Field";
import Input from "../../../components/form/Input";
import Select from "../../../components/form/Select";
type Workflow = components["schemas"]["WorkflowRead"];
const blank = { nodes: [{ id: "start", kind: "start" as const, name: "Start" }, { id: "end", kind: "end" as const, name: "End" }], edges: [] };
export default function WorkflowList({ navigate }: {
    navigate: (href: string) => void;
}) {
    const [rows, setRows] = useState<Workflow[]>([]), [search, setSearch] = useState(""), [filter, setFilter] = useState(""), [cursor, setCursor] = useState<string>(), [next, setNext] = useState<string | null>(null), [pages, setPages] = useState<(string | undefined)[]>([]);
    const [loading, setLoading] = useState(true), [error, setError] = useState(""), [retry, setRetry] = useState(0), [history, setHistory] = useState<Workflow>(), [creating, setCreating] = useState(false);
    useEffect(() => { let active = true; void Promise.resolve().then(() => { if (active) {
        setLoading(true);
        setError("");
    } }); void apiClient.GET("/api/v1/workflows", { params: { query: { search: filter, cursor } } }).then(r => { if (!active)
        return; if (r.data) {
        setRows(r.data.rows);
        setNext(r.data.next_cursor);
    }
    else
        setError("Workflows could not load. Your search is preserved. Check your connection and retry."); setLoading(false); }).catch(() => { if (active) {
        setError("Workflows could not load. Your search is preserved. Retry.");
        setLoading(false);
    } }); return () => { active = false; }; }, [filter, cursor, retry]);
    return <section className="wfl" aria-label="Approval Workflows"><header><h2>Approval Workflows</h2><button onClick={() => setCreating(true)}><Plus aria-hidden="true"/>New Workflow</button></header><div className="wfl-card"><form onSubmit={e => { e.preventDefault(); setCursor(undefined); setPages([]); setFilter(search); }}><label htmlFor="workflow-search">Search workflows</label><input id="workflow-search" value={search} onChange={e => setSearch(e.target.value)}/><button><MagnifyingGlass aria-hidden="true"/>Search</button></form>{loading ? <p role="status" className="wfl-loading">Loading workflows…</p> : error ? <p role="alert">{error}<button onClick={() => setRetry(n => n + 1)}>Retry</button></p> : rows.length === 0 ? <><h3>{filter ? "No Matching Workflows" : "No Workflows Yet"}</h3><p>{filter ? "Change your search to find a workflow." : "A document type with no published workflow cannot be submitted."}</p><button onClick={() => filter ? (setSearch(""), setFilter("")) : setCreating(true)}>{filter ? "Clear search" : "Add your first workflow"}</button></> : <>{rows.some(r => r.in_flight === null) && <p role="status">In-flight counts delayed</p>}<table><caption>Workflow definitions</caption><thead><tr>{["Workflow", "Applies To", "Published", "Draft", "In Flight", "Actions"].map(h => <th key={h}>{h}</th>)}</tr></thead><tbody>{rows.map(row => <tr key={row.id}><th scope="row"><button onClick={() => navigate(`/admin/approval-workflows/${row.id}`)}>{row.name}</button><p>Edited <time dateTime={row.edited_at ?? row.created_at}>{formatDate(row.edited_at ?? row.created_at)}</time> by {row.edited_by_name ?? "Unknown"}</p></th><td data-label="Applies To">{row.applies_to}</td><td data-label="Published"><Pill state={row.published_version ? "published" : "neutral"}>{row.published_version ? `Version ${row.published_version}` : "Not Published"}</Pill></td><td data-label="Draft"><Pill state="draft">{row.draft_version ? `Draft ${row.draft_version}` : "None"}</Pill></td><td data-label="In Flight">{row.in_flight ?? "Not available"}</td><td data-label="Actions"><button aria-label={`Versions for ${row.name}`} onClick={() => setHistory(row)}>Versions</button></td></tr>)}</tbody></table></>}<nav aria-label="Workflow pages">{pages.length > 0 && <button onClick={() => { setCursor(pages.at(-1)); setPages(p => p.slice(0, -1)); }}>Previous page</button>}{next && <button onClick={() => { setPages(p => [...p, cursor]); setCursor(next); }}>Next page</button>}</nav></div>{history && <History workflow={history} onClose={() => setHistory(undefined)}/>}{creating && <NewWorkflow navigate={navigate} onClose={() => setCreating(false)}/>}</section>;
}
function NewWorkflow({ navigate, onClose }: {
    navigate: (href: string) => void;
    onClose: () => void;
}) {
    const [name, setName] = useState(""), [type, setType] = useState<components["schemas"]["WorkflowCreate"]["document_type"]>("requisition"), [unit, setUnit] = useState(""), [units, setUnits] = useState<components["schemas"]["OrgUnitRead"][]>([]), [cursor, setCursor] = useState<string | null>(null), [unitError, setUnitError] = useState(""), [error, setError] = useState(""), [fieldError, setFieldError] = useState<string>(), [busy, setBusy] = useState(false);
    const [focusRequest, setFocusRequest] = useState(0);
    const alert = useRef<HTMLDivElement>(null), created = useRef<Workflow>(undefined), key = useRef(crypto.randomUUID());
    useEffect(() => { if (focusRequest) alert.current?.focus(); }, [focusRequest]);
    async function load(next?: string) { try { const r = await apiClient.GET("/api/v1/org/units", { params: { query: { active: true, cursor: next } } }); if (r.data) {
        setUnits(old => next ? [...old, ...r.data!.rows] : r.data!.rows);
        setCursor(r.data.next_cursor);
        setUnitError("");
    }
    else
        setUnitError("Scopes could not load. Your entries are preserved. Try again."); } catch { setUnitError("Scopes could not load. Your entries are preserved. Try again."); } }
    useEffect(() => { void Promise.resolve().then(() => load()); }, []);
    async function create() { setBusy(true); setError(""); setFieldError(undefined); try {
        let w = created.current;
        if (!w) {
            const r = await apiClient.POST("/api/v1/workflows", { headers: { ...writeHeaders(), "Idempotency-Key": key.current }, body: { name, document_type: type, scope_type: unit ? "bu" : "org", ...(unit ? { scope_id: unit } : {}) } });
            if (!r.data) {
                setFieldError(r.error?.errors?.find(e => e.field === "/name")?.message);
                throw new Error(r.error?.detail ?? "Workflow could not be created.");
            }
            w = r.data;
            created.current = w;
        }
        const d = await apiClient.POST("/api/v1/workflows/{id}/versions", { params: { path: { id: w.id } }, headers: { ...writeHeaders(), "Idempotency-Key": `${key.current}-draft` }, body: { graph: blank } });
        if (!d.data)
            throw new Error(d.error?.detail ?? "The workflow was created, but its draft could not load. Retry to complete it.");
        navigate(`/admin/approval-workflows/${w.id}`);
    }
    catch (e) {
        setError(`${e instanceof Error ? e.message : "Creation failed."} Your entries are preserved. Correct the fields and retry.`);
        setFocusRequest(n => n + 1);
    }
    finally {
        setBusy(false);
    } }
    return <Sheet title="New Workflow" onClose={onClose}><form noValidate onSubmit={e => { e.preventDefault(); void create(); }}>{error && <div role="alert" tabIndex={-1} ref={alert}>{error}{fieldError && <a href="#workflow-name">Name: {fieldError}</a>}</div>}<Field id="workflow-name" label="Name" required error={fieldError} help="Use a descriptive workflow name."><Input required inputMode="text" autoComplete="off" value={name} onChange={e => { setName(e.target.value); key.current = crypto.randomUUID(); }}/><Check aria-hidden="true" className="wfl-complete" weight="regular" /></Field><Field label="Document type" help="Choose which documents this workflow routes."><Select value={type} onChange={e => { setType(e.target.value as typeof type); key.current = crypto.randomUUID(); }}>{["requisition", "po", "po_change", "project", "budget_change"].map(t => <option key={t}>{t}</option>)}</Select></Field><Field label="Scope" help="Choose the organization or a business unit / organizational unit."><Select value={unit} onChange={e => { setUnit(e.target.value); key.current = crypto.randomUUID(); }}><option value="">Organization</option>{units.map(u => <option key={u.id} value={u.id}>{u.name}</option>)}</Select></Field>{unitError && <p role="alert">{unitError}<button type="button" onClick={() => void load()}>Retry scopes</button></p>}{cursor && <button type="button" onClick={() => void load(cursor)}>More scopes</button>}<button aria-busy={busy}>Create Workflow</button></form></Sheet>;
}

function formatDate(value: string): string {
    return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(value));
}
