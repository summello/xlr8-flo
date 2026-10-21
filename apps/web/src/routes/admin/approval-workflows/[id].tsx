import * as Dialog from "@radix-ui/react-dialog";
import { WarningCircle, Trash } from "@phosphor-icons/react";
import { useCallback, useEffect, useRef, useState } from "react";
import { apiClient } from "../../../api/client";
import type { paths, components } from "../../../api/generated/schema";
import { writeHeaders } from "../../../features/auth/api";
import DeleteDraftDialog from "../../../components/drafts/DeleteDraftDialog";
import { mutationHeaders, type Problem } from "../../../components/requisitions/api";
import { Canvas, Connections, Outline } from "../../../components/workflows/Editors";
import { operate, type Graph, type Node, type Operation } from "../../../components/workflows/graph-reducer";
import Preview from "../../../components/workflows/preview/Preview";
import Field from "../../../components/form/Field";
import Input from "../../../components/form/Input";
import Select from "../../../components/form/Select";
import "../../../styles/workflows.css";
type Workflow = components["schemas"]["WorkflowRead"];
type Version = components["schemas"]["WorkflowVersionRead"];
type Report = components["schemas"]["ValidationReport"];
type Picker = components["schemas"]["PickerRow"];
const blank: Graph = { nodes: [{ id: "start", kind: "start", name: "Start" }, { id: "end", kind: "end", name: "End" }], edges: [], layout: {} };
export default function WorkflowBuilder({ id, navigate }: {
    id: string;
    navigate: (href: string) => void;
}) {
    const [previewOpen, setPreviewOpen] = useState(false);
    const [previewGraph, setPreviewGraph] = useState<Graph>();
    const [previewSteps, setPreviewSteps] = useState<string[]>([]);
    const [previewEdges, setPreviewEdges] = useState<number[]>([]);
    const [workflow, setWorkflow] = useState<Workflow>();
    const [version, setVersion] = useState<Version>();
    const [graph, setGraph] = useState<Graph>(blank);
    const [selected, setSelected] = useState("start");
    const [view, setView] = useState<"canvas" | "outline">(() => matchMedia("(max-width: 767px)").matches ? "outline" : "canvas");
    const [panel, setPanel] = useState<"inspector" | "problems">("inspector");
    const [catalog, setCatalog] = useState<Record<string, paths["/api/v1/workflows/catalog"]["get"]["responses"][200]["content"]["application/json"][string]>>({});
    const [people, setPeople] = useState<Picker[]>([]);
    const [peopleError, setPeopleError] = useState("");
    const [error, setError] = useState("");
    const [stale, setStale] = useState(false);
    const [loading, setLoading] = useState(true);
    const [report, setReport] = useState<Report>({ errors: [], warnings: [] });
    const [dialog, setDialog] = useState(false);
    const [busy, setBusy] = useState(false);
    const [status, setStatus] = useState("");
    const [depth, setDepth] = useState("Not available");
    const alert = useRef<HTMLDivElement>(null), inspector = useRef<HTMLHeadingElement>(null), problems = useRef<HTMLDivElement>(null), heading = useRef<HTMLHeadingElement>(null);
    const current = useRef(graph), saved = useRef(graph);
    const [conditions, setConditions] = useState<Record<string, NonNullable<components["schemas"]["Edge"]["when"]>>>({});
    const loadPeople = useCallback(async (w: Workflow) => { try {
        const r = await apiClient.GET("/api/v1/approvals/eligible-approvers", { params: { query: { document_type: w.document_type, scope_type: w.scope_type, scope_id: w.scope_id } } });
        if (!r.data)
            throw new Error(r.error?.detail ?? "Eligible approvers could not load.");
        setPeople(r.data.rows);
        setPeopleError("");
    }
    catch (e) {
        setPeopleError(`${e instanceof Error ? e.message : "Approvers could not load."} Current selections are preserved. Try again.`);
    } }, []);
    const load = useCallback(async () => {
        setLoading(true);
        setError("");
        try {
            const w = await apiClient.GET("/api/v1/workflows/{id}", { params: { path: { id } } });
            if (!w.data)
                throw new Error(w.error?.detail ?? "Could not load this workflow. Check access or try again.");
            setWorkflow(w.data);
            void loadPeople(w.data);
            const c = await apiClient.GET("/api/v1/workflows/catalog", { params: { query: { document_type: w.data.document_type } } });
            if (!c.data)
                throw new Error("Could not load the attribute catalogue. Try again.");
            setCatalog(c.data);
            const versions = [...(w.data.versions ?? [])].sort((a, b) => b.version - a.version), draft = versions.find(v => v.status === "draft"), published = versions.find(v => v.status === "published");
            let g = blank;
            if (draft || published) {
                const r = await apiClient.GET("/api/v1/workflows/{id}/versions/{version}", { params: { path: { id, version: (draft || published)!.version } } });
                if (!r.data)
                    throw new Error(r.error?.detail ?? "Could not load the version. Try again.");
                g = r.data.graph;
                if (draft)
                    setVersion(r.data);
            }
            if (!draft) {
                const r = await apiClient.POST("/api/v1/workflows/{id}/versions", { params: { path: { id } }, body: { graph: g }, headers: writeHeaders() });
                if (!r.data)
                    throw new Error(r.error?.detail ?? "Could not create a draft. Try again.");
                setVersion(r.data);
            }
            setGraph(g);
            current.current = g;
            saved.current = g;
            setStale(false);
            setReport({ errors: [], warnings: [] });
            setConditions({});
            const units = await apiClient.GET("/api/v1/org/units", { params: { query: { page_size: 1 } } });
            const unit = units.data?.rows[0];
            if (unit) {
                const setting = await apiClient.GET("/api/v1/org/units/{id}/settings/{key}/effective", { params: { path: { id: unit.id, key: "approval.max_depth" } } });
                if (setting.data)
                    setDepth(String(setting.data.value));
            }
        }
        catch (e) {
            setError(e instanceof Error ? e.message : "Could not load this workflow. Try again.");
        }
        finally {
            setLoading(false);
        }
    }, [id, loadPeople]);
    useEffect(() => { let cancelled = false; queueMicrotask(() => { if (!cancelled)
        void load(); }); return () => { cancelled = true; }; }, [load]);
    useEffect(() => { const handler = (e: BeforeUnloadEvent) => { if (current.current !== saved.current)
        e.preventDefault(); }; window.addEventListener("beforeunload", handler); return () => window.removeEventListener("beforeunload", handler); }, []);
    // Focus the summary after the problems panel has committed, not on a guessed frame.
    const [problemsFocus, setProblemsFocus] = useState(0);
    useEffect(() => { if (problemsFocus > 0)
        problems.current?.focus(); }, [problemsFocus]);
    function focusAlert(message: string) { setError(message); requestAnimationFrame(() => alert.current?.focus()); }
    function select(nodeId: string, edit = false) { setSelected(nodeId); setPanel("inspector"); if (edit)
        requestAnimationFrame(() => inspector.current?.focus()); }
    function dispatch(op: Operation) { const result = operate(current.current, op); if (result.error) {
        focusAlert(result.error);
        return;
    } let next = result.graph; if (op.type === "connect" && op.output === "yes" && conditions[op.from])
        next = operate(next, { type: "condition", id: op.from, when: conditions[op.from]! }).graph; current.current = next; setGraph(next); setReport({ errors: [], warnings: [] }); if (op.type === "delete") {
        setSelected("");
        requestAnimationFrame(() => heading.current?.focus());
    } }
    const node = graph.nodes.find(n => n.id === selected);
    const when = (node ? graph.edges.find(e => e.from === node.id && e.when)?.when ?? conditions[node.id] : undefined) ?? { field: "", op: "gt", value: { amount: "", currency: "" } };
    const money = typeof when.value === "object" && when.value && !Array.isArray(when.value) ? when.value as {
        amount?: string;
        currency?: string;
    } : {};
    function condition(fields: Record<string, unknown>) { if (!node)
        return; const value = { ...when, ...fields } as NonNullable<components["schemas"]["Edge"]["when"]>; setConditions(previous => ({ ...previous, [node.id]: value })); dispatch({ type: "condition", id: node.id, when: value }); }
    function add(kind: Node["kind"]) { const nodeId = `${kind}_${crypto.randomUUID().slice(0, 8)}`; dispatch({ type: "add", node: { id: nodeId, kind, name: kind === "approval" ? "Approval Step" : kind === "condition" ? "Condition" : kind === "end" ? "End" : "Start", ...(kind === "approval" ? { approvers: { type: "users", user_ids: [] }, mode: "any" } : {}) } }); select(nodeId, true); }
    async function check(publish = false) {
        if (!version || busy)
            return;
        const errors: NonNullable<Report["errors"]> = [];
        for (const n of current.current.nodes) {
            if (["approval", "condition"].includes(n.kind) && !n.name?.trim())
                errors.push({ code: "missing_name", node_id: n.id, detail: "Enter a name for this node." });
            if (n.kind === "approval" && n.approvers?.type === "users" && !n.approvers.user_ids.length)
                errors.push({ code: "missing_approvers", node_id: n.id, detail: "Select at least one eligible approver." });
            if (n.kind === "condition") {
                const expression = current.current.edges.find(e => e.from === n.id && e.when)?.when ?? conditions[n.id];
                if (!expression?.value || typeof expression.value !== "object" || Array.isArray(expression.value) || !String((expression.value as { amount?: unknown }).amount ?? "").trim())
                    errors.push({ code: "missing_value", node_id: n.id, detail: "Enter a condition value." });
            }
        }
        setBusy(true);
        setError("");
        setStatus("Saving and checking draft…");
        try {
            const r = await apiClient.PUT("/api/v1/workflows/{id}/versions/{version}", { params: { path: { id, version: version.version } }, headers: { ...writeHeaders(), "If-Match": String(version.row_version) }, body: { graph: current.current } });
            if (!r.data) {
                if (r.response.status === 409 && r.error?.checks?.problem === "stale_version") {
                    setStale(true);
                    throw new Error("Someone else changed this draft. Reload it.");
                }
                throw new Error(r.error?.detail ?? "Could not save this draft. Your graph is preserved. Correct the issue and retry.");
            }
            setVersion(r.data);
            saved.current = current.current;
            const v = await apiClient.GET("/api/v1/workflows/{id}/versions/{version}/validation", { params: { path: { id, version: version.version } } });
            if (!v.data)
                throw new Error(v.error?.detail ?? "Check failed. Your draft is saved. Try again.");
            // Field hints from the form join the server report; they never skip the save.
            const merged = { errors: [...errors, ...(v.data.errors ?? [])], warnings: v.data.warnings ?? [] };
            setReport(merged);
            setPanel("problems");
            setStatus("Draft saved and checked.");
            if (publish && !merged.errors.length)
                setDialog(true);
            else
                setProblemsFocus(tick => tick + 1);
        }
        catch (e) {
            focusAlert(e instanceof Error ? e.message : "Save failed. Your graph is preserved. Try again.");
            setStatus("");
        }
        finally {
            setBusy(false);
        }
    }
    async function publish() { if (!version || busy)
        return; setBusy(true); try {
        const r = await apiClient.POST("/api/v1/workflows/{id}/versions/{version}/publish", { params: { path: { id, version: version.version } }, headers: writeHeaders(), body: { acknowledge_warnings: (report.warnings ?? []).length > 0 } });
        if (!r.data)
            throw new Error(r.error?.detail ?? "Publishing failed. Your draft is preserved. Check it and try again.");
        setVersion(r.data);
        setDialog(false);
        setStatus(`Version ${r.data.version} published.`);
    }
    catch (e) {
        setDialog(false);
        focusAlert(e instanceof Error ? e.message : "Publishing failed. Try again.");
    }
    finally {
        setBusy(false);
    } }
    const issues = [...(report.errors ?? []), ...(report.warnings ?? [])], readonly = version?.status !== "draft";
    return <section className="wf-builder" aria-label="Approval workflow builder"><header><h2>{workflow?.name ?? "Approval workflow"}</h2><p>{workflow ? `Applies to ${workflow.document_type} · ${version?.status ?? "Draft"} version ${version?.version ?? ""}` : "Loading workflow…"}</p><p>Approval depth limit: {depth}. In-flight count: Not available.</p><button onClick={() => { if (current.current !== saved.current) void check().then(() => { if (current.current === saved.current) setPreviewOpen(true); }); else setPreviewOpen(true); }}>Route Preview</button>{!readonly && <><button onClick={() => void check()} aria-busy={busy}>Check</button><button className="form-submit" onClick={() => void check(true)} aria-busy={busy}>Publish</button>
  <DeleteDraftDialog kind="Workflow Version" reference={`${workflow?.name ?? "This workflow"} version ${version?.version}`} onDone={() => navigate("/admin/approval-workflows")}
    remove={async key => { const { response, error } = await apiClient.DELETE("/api/v1/workflows/{id}/versions/{version}", { params: { path: { id, version: version!.version } }, headers: mutationHeaders(key) }); return { ok: response.ok, error: error as Problem | undefined }; }} /></>}</header>
 {error && <div role="alert" tabIndex={-1} ref={alert}><WarningCircle aria-hidden="true"/>{error}{stale ? <button onClick={() => void load()}>Reload draft</button> : !workflow ? <button onClick={() => void load()}>Retry loading workflow</button> : null}</div>}<p role="status">{status}</p>
 {loading ? <div className="wf-loading" role="status">Loading workflow and draft…</div> : workflow && <div className="wf-columns"><section className="wf-card"><h3 ref={heading} tabIndex={-1}>Workflow</h3>
 <div role="tablist" aria-label="Workflow view">{(["canvas", "outline"] as const).map(v => <button key={v} role="tab" id={`wf-${v}-tab`} aria-controls="wf-editor" aria-selected={view === v} tabIndex={view === v ? 0 : -1} onClick={() => setView(v)} onKeyDown={e => { if (["ArrowLeft", "ArrowRight"].includes(e.key)) {
            e.preventDefault();
            const next = v === "canvas" ? "outline" : "canvas";
            setView(next);
            document.getElementById(`wf-${next}-tab`)?.focus();
        } }}>{v === "canvas" ? "Canvas" : "Outline"}</button>)}</div>
 <fieldset disabled={readonly}><legend>Build workflow</legend><div className="wf-toolbar"><button onClick={() => add("condition")}>Add Condition</button><button onClick={() => add("approval")}>Add Approval Step</button>{!graph.nodes.some(n => n.kind === "start") && <button onClick={() => add("start")}>Add Start</button>}<button onClick={() => add("end")}>Add End</button><button onClick={() => { if (node)
            dispatch({ type: "delete", id: node.id }); }}><Trash aria-hidden="true"/>Delete Selected</button></div>{graph.nodes.every(n => ["start", "end"].includes(n.kind)) && <p>Nothing Between Start and End</p>}
 <div role="tabpanel" id="wf-editor" aria-labelledby={`wf-${view}-tab`}>{view === "canvas" ? <Canvas graph={graph} selected={selected} select={select} dispatch={dispatch} issues={issues} previewEdges={previewGraph === graph ? previewEdges : []} previewSteps={previewGraph === graph ? previewSteps : []}/> : <Outline graph={graph} selected={selected} select={select} dispatch={dispatch} issues={issues}/>}</div></fieldset></section>
 <section className="wf-card wf-inspector"><div role="tablist" aria-label="Workflow details">{(["inspector", "problems"] as const).map(p => <button key={p} role="tab" id={`wf-${p}-tab`} aria-controls="wf-details" aria-selected={panel === p} tabIndex={panel === p ? 0 : -1} onClick={() => setPanel(p)} onKeyDown={e => { if (["ArrowLeft", "ArrowRight"].includes(e.key)) {
            e.preventDefault();
            const next = p === "inspector" ? "problems" : "inspector";
            setPanel(next);
            document.getElementById(`wf-${next}-tab`)?.focus();
        } }}>{p === "inspector" ? "Inspector" : `Problems ${issues.length}`}</button>)}</div><div role="tabpanel" id="wf-details" aria-labelledby={`wf-${panel}-tab`}>
 {panel === "problems" ? <div ref={problems} tabIndex={-1}><h3>{(report.errors ?? []).length ? `Not ready to publish. ${(report.errors ?? []).length} problems. Publishing is blocked until each one is fixed.` : "No blocking problems"}</h3>{issues.map((i, j) => <div key={j}><WarningCircle aria-hidden="true"/><strong>{(report.errors ?? []).includes(i) ? "Blocks" : "Warning"}: {i.code.replaceAll("_", " ")}</strong><p>{i.detail}</p>{i.node_id && <button onClick={() => select(i.node_id!, true)}>Go to Node {graph.nodes.find(n => n.id === i.node_id)?.name || i.node_id}</button>}</div>)}</div> : <><h3 ref={inspector} tabIndex={-1}>Inspector: {node?.name || node?.id || "Select a node"}</h3><fieldset disabled={readonly}><legend>Node settings</legend>
 {node?.kind === "start" && <p>Funds are reserved at submission for project-linked requests. Funding is assigned when a standalone request is approved. This workflow chooses approvers.</p>}
 {node && ["approval", "condition"].includes(node.kind) && <Field label="Name" error={report.errors?.find(i => i.node_id === node.id && i.code === "missing_name")?.detail} required help="Give this node a descriptive name."><Input required autoComplete="off" inputMode="text" value={node.name ?? ""} onChange={e => dispatch({ type: "update", id: node.id, fields: { name: e.target.value } })}/></Field>}
 {node?.kind === "condition" && <><Field label="Attribute" help="Only fields exposed by the server can be used."><Select value={String(when.field ?? "")} onChange={e => condition({ field: e.target.value })}><option value="">Choose an attribute</option>{Object.keys(catalog).map(f => <option key={f} value={f}>{f === "amount" ? "Estimate" : f}</option>)}</Select></Field><Field label="Test" help="Chain two conditions for a range."><Select value={String(when.op)} onChange={e => condition({ op: e.target.value })}><option value="gt">is over</option><option value="le">is at most</option><option value="eq">is</option></Select></Field><Field label="Value" error={report.errors?.find(i => i.node_id === node.id && i.code === "missing_value")?.detail} required help="Enter the exact decimal amount, up to four decimal places."><Input required autoComplete="off" inputMode="decimal" value={money.amount ?? ""} onChange={e => condition({ value: { ...money, amount: e.target.value } })}/></Field><Field label="Currency code" help="Use the document currency (for example USD)."><Input autoComplete="off" inputMode="text" value={money.currency ?? ""} onChange={e => condition({ value: { ...money, currency: e.target.value.toUpperCase() } })}/></Field></>}
 {node?.kind === "approval" && <><fieldset><legend>Approvers</legend><p>Approvers are checked when each decision is made. Requesters cannot approve their own request.</p>{peopleError ? <div role="alert">{peopleError} Current approvers: {node.approvers?.type === "users" ? node.approvers.user_ids.join(", ") : node.approvers?.type}.<button onClick={() => void loadPeople(workflow)}>Try again</button></div> : people.map(person => <label key={person.user_id}><input type="checkbox" checked={node.approvers?.type === "users" && node.approvers.user_ids.includes(person.user_id)} onChange={e => { const ids = node.approvers?.type === "users" ? node.approvers.user_ids : [], next = e.target.checked ? [...ids, person.user_id] : ids.filter(id => id !== person.user_id); dispatch({ type: "update", id: node.id, fields: { approvers: { type: "users", user_ids: next } } }); }}/>{person.email}</label>)}{node.approvers?.type !== "users" && <p>All eligible permission holders (current rule: {node.approvers?.type}). Select people to use a named group.</p>}</fieldset>
 <fieldset><legend>Group Rule</legend>{["any", "all", "quorum"].map(mode => <label key={mode}><input type="radio" name="group-rule" checked={mode === "quorum" ? typeof node.mode === "object" : node.mode === mode} onChange={() => dispatch({ type: "update", id: node.id, fields: { mode: mode === "quorum" ? { quorum: 1 } : mode as "any" | "all" } })}/>{mode === "any" ? "Any one approves" : mode === "all" ? "All approve" : "Quorum"}</label>)}</fieldset>
 {typeof node.mode === "object" && node.mode && <Field label="Approvals Needed" help="Choose how many current approvers must approve."><Input type="number" min={1} autoComplete="off" inputMode="numeric" value={node.mode.quorum} onChange={e => dispatch({ type: "update", id: node.id, fields: { mode: { quorum: Number(e.target.value) } } })}/></Field>}
 <label><input type="checkbox" checked={node.allow_repeat ?? false} onChange={e => dispatch({ type: "update", id: node.id, fields: { allow_repeat: e.target.checked } })}/>An approver here may also approve at an earlier step</label>
 {(["due_days", "escalate_after_days"] as const).map(field => <Field key={field} label={field === "due_days" ? "Due After (Days)" : "Escalate After (Days)"} help="Leave empty to omit. Use a positive whole number."><Input type="number" min={1} autoComplete="off" inputMode="numeric" value={node[field] ?? ""} onChange={e => dispatch({ type: "update", id: node.id, fields: { [field]: e.target.value ? Number(e.target.value) : null } })}/></Field>)}
 <Field label="Escalate To" help="Choose an eligible person, or leave escalation unset."><Select value={node.escalate_to?.type === "users" ? node.escalate_to.user_ids[0] : ""} onChange={e => dispatch({ type: "update", id: node.id, fields: { escalate_to: e.target.value ? { type: "users", user_ids: [e.target.value] } : null } })}><option value="">Not set</option>{people.map(p => <option key={p.user_id} value={p.user_id}>{p.email}</option>)}</Select></Field></>}
 {node && <Connections node={node} graph={graph} dispatch={dispatch}/>}</fieldset></>}
 </div></section></div>}
 {previewOpen && workflow && version && <Preview workflow={workflow} version={version.version} onClose={() => setPreviewOpen(false)} onRoute={r => { setPreviewGraph(graph); setPreviewEdges(r.path_edge_indices ?? []); setPreviewSteps(r.steps.map(s => s.node_id)); }} />}
 <Dialog.Root open={dialog} onOpenChange={setDialog}><Dialog.Portal><Dialog.Overlay className="wf-scrim"/><Dialog.Content className="wf-dialog" onOpenAutoFocus={e => { e.preventDefault(); document.getElementById("wf-keep")?.focus(); }}><Dialog.Title>Publish Version {version?.version}?</Dialog.Title><Dialog.Description>New submissions use this version. In-flight documents keep their existing version, routing and history. In-flight count: Not available. A published version cannot be edited; publishing is recorded in the audit history.</Dialog.Description>{(report.warnings ?? []).map((w, i) => <p key={i}><WarningCircle aria-hidden="true"/>{w.detail}</p>)}<Dialog.Close asChild><button id="wf-keep">Keep Editing</button></Dialog.Close><button onClick={() => void publish()}>{(report.warnings ?? []).length ? "Acknowledge warnings and Publish" : "Confirm Publish"}</button></Dialog.Content></Dialog.Portal></Dialog.Root>
 </section>;
}
