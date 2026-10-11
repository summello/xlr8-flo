import { useCallback, useEffect, useState } from "react";
import { apiClient } from "../../api/client";
import type { components } from "../../api/generated/schema";
import { writeHeaders } from "../../features/auth/api";
import RecordForm, { type RecordField } from "./RecordForm";
import { failure, label, WriteFailure, type Risk } from "./api";
const fields: RecordField[] = [
  { key: "title", label: "Risk title", help: "Summarize the risk.", required: true },
  { key: "description", label: "Description", help: "Describe the risk and its consequences." },
  { key: "likelihood", label: "Likelihood", help: "Whole number from 1 to 5.", type: "number", min: 1, max: 5, required: true },
  { key: "impact", label: "Impact", help: "Whole number from 1 to 5.", type: "number", min: 1, max: 5, required: true },
  { key: "owner_id", label: "Risk owner", help: "Identity identifier of the responsible organization member.", required: true },
  { key: "mitigation", label: "Mitigation", help: "Describe how the risk will be managed." },
  { key: "due_date", label: "Risk due date", help: "Optional mitigation due date.", type: "date" },
  { key: "status", label: "Risk status", help: "Closing a risk requires a reason.", options: ["open", "mitigating", "closed", "accepted"] },
  { key: "closed_reason", label: "Closure reason", help: "Required when closing a risk." },
];
export default function Risks({ id, owner }: { id: string; owner: string }) {
  const [rows, setRows] = useState<Risk[] | null>(null);
  const [status, setStatus] = useState<Risk["status"] | "">("");
  const [score, setScore] = useState("");
  const [cursor, setCursor] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [editor, setEditor] = useState<Risk | "new" | null>(null);
  const load = useCallback(async (next?: string) => {
    try {
      const result = await apiClient.GET("/api/v1/projects/{id}/risks", { params: { path: { id }, query: { status: status || undefined, min_score: score ? Number(score) : undefined, page_size: 50, cursor: next } } });
      setError(null);
      if (result.data) { const data = result.data; setRows(current => next ? [...(current ?? []), ...data.rows] : data.rows); setCursor(data.next_cursor); }
      else setError(failure(result.error, "Loading risks"));
    } catch { setError(failure(undefined, "Loading risks")); }
    finally { setLoading(false); }
  }, [id, status, score]);
  // load updates state only after network responses; this effect starts the request.
  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => { void load(); }, [load]);
  const save = async (v: Record<string, string>) => {
    const body: components["schemas"]["RiskCreate"] = {
      title: v.title!, description: v.description || null, likelihood: Number(v.likelihood), impact: Number(v.impact),
      owner_id: v.owner_id!, mitigation: v.mitigation || null, due_date: v.due_date || null,
      status: (v.status || "open") as Risk["status"], closed_reason: v.closed_reason || null,
    };
    if (body.status === "closed" && !body.closed_reason?.trim()) throw new WriteFailure({ correlation_id: "client-validation", errors: [{ field: "closed_reason", message: "A closure reason is required. Your entries are preserved; explain the resolution." }] }, "Closing risk");
    const result = editor && editor !== "new" ? await apiClient.PATCH("/api/v1/projects/{id}/risks/{risk_id}", { params: { path: { id, risk_id: editor.id } }, headers: { ...writeHeaders(), "If-Match": String(editor.version) }, body })
      : await apiClient.POST("/api/v1/projects/{id}/risks", { params: { path: { id } }, headers: writeHeaders(), body });
    if (!result.data) throw new WriteFailure(result.error, "Saving risk");
    setEditor(null); setLoading(true); await load();
  };
  return <section aria-label="Project risks"><div className="project-toolbar">
    <label>Risk status filter<select aria-label="Risk status filter" value={status} onChange={e => { setLoading(true); setStatus(e.target.value as typeof status); }}><option value="">All statuses</option>{["open", "mitigating", "closed", "accepted"].map(s => <option key={s}>{s}</option>)}</select></label>
    <label>Minimum score<input type="number" min={1} max={25} value={score} onChange={e => { setLoading(true); setScore(e.target.value); }} /></label><button onClick={() => setEditor("new")}>Create risk</button>
  </div>
    {loading && <p role="status">Loading risks… Previously loaded rows are preserved.</p>}
    {error && <div role="alert"><p>{error}</p><button onClick={() => { setLoading(true); void load(); }}>Retry risks</button></div>}
    {editor && <RecordForm key={editor === "new" ? "new" : editor.id} title={editor === "new" ? "Create risk" : "Edit risk"} fields={fields}
      initial={editor === "new" ? { owner_id: owner, status: "open" } : Object.fromEntries(Object.entries(editor).map(([k, v]) => [k, v === null ? "" : String(v)]))} save={save} cancel={() => setEditor(null)} />}
    {rows?.length === 0 && <p>No risks match this view. Create a risk or change the filters.</p>}
    {rows && rows.length > 0 && <ul className="project-records">{rows.map(row => <li key={row.id}><strong>{row.title}</strong><p>{label(row.status ?? "unknown")} · Score {row.score} · {row.overdue ? "Overdue" : "Due"} {row.due_date ?? "Unscheduled"}</p><p>{row.mitigation ?? "No mitigation recorded"}</p><button onClick={() => setEditor(row)}>Edit {row.title}</button></li>)}</ul>}
    {cursor && <button disabled={loading} onClick={() => { setLoading(true); void load(cursor); }}>Load more risks</button>}
  </section>;
}
