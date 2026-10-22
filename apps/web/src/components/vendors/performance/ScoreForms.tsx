import { useId, useRef, useState, type ReactNode } from "react";
import { apiClient } from "../../../api/client";
import ErrorSummary, { type SummaryError } from "../../form/ErrorSummary";
import { failure, mutationHeaders } from "../../requisitions/api";
import { CORE_CATEGORIES, monthStart, type Dispute, type Score } from "./api";

type Result = { error?: unknown; response: Response };

/** null when the server accepted it; otherwise words for the refusal. The server decides who may act (A11Y/UX-005). */
async function outcome(run: () => Promise<Result>, action: string): Promise<string | null> {
  const result = await run().catch(() => undefined);
  if (result?.response.ok) return null;
  if (result?.response.status === 403) return "You do not have permission to do this. Nothing was changed.";
  return failure(result?.error as { detail?: string } | undefined, action);
}

type Rule = [field: string, label: string, empty: boolean];

/** Shared shell: required-field check, error summary, the server's refusal, input kept on failure (A11Y-006). */
function Form({ heading, submitLabel, rules, send, onDone, onCancel, children }: {
  heading: string; submitLabel: string; rules: Rule[]; send: () => Promise<string | null>; onDone: () => void; onCancel: () => void;
  children: (props: (field: string) => { id: string; "aria-invalid"?: true; "aria-describedby"?: string; "aria-required": true }, error: (field: string) => ReactNode) => ReactNode;
}) {
  const base = useId();
  const [errors, setErrors] = useState<SummaryError[]>([]);
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const alertRef = useRef<HTMLParagraphElement>(null);
  const fid = (field: string) => `${base}-${field}`;
  const message = (field: string) => errors.find(e => e.fieldId === fid(field))?.message;
  async function submit() {
    const found = rules.filter(r => r[2]).map(([field, label]) => ({ fieldId: fid(field), label, message: "Required." }));
    setErrors(found); setProblem(null);
    if (found.length) { setTimeout(() => document.getElementById(found[0]!.fieldId)?.focus()); return; }
    setBusy(true);
    const result = await send();
    setBusy(false);
    if (result) { setProblem(result); setTimeout(() => alertRef.current?.focus()); return; }
    onDone();
  }
  return <form className="perf-form" noValidate onSubmit={e => { e.preventDefault(); void submit(); }}>
    <h3>{heading}</h3>
    <ErrorSummary errors={errors} />
    {problem && <p role="alert" tabIndex={-1} ref={alertRef} className="field-error">{problem}</p>}
    {children(
      field => ({ id: fid(field), "aria-required": true, ...(message(field) ? { "aria-invalid": true as const, "aria-describedby": `${fid(field)}-err` } : {}) }),
      field => message(field) ? <p id={`${fid(field)}-err`} className="field-error">{message(field)}</p> : null,
    )}
    <div className="appr-actions">
      <button type="submit" aria-busy={busy} disabled={busy}>{submitLabel}</button>
      <button type="button" onClick={onCancel}>Cancel</button>
    </div>
  </form>;
}

type Common = { vendorId: string; onDone: (message: string) => void; onCancel: () => void };

export function AddScoreForm({ vendorId, categories, onDone, onCancel }: Common & { categories: string[] }) {
  const [category, setCategory] = useState(""), [score, setScore] = useState(""), [period, setPeriod] = useState("");
  const [source, setSource] = useState(""), [rationale, setRationale] = useState("");
  const options = [...new Set([...CORE_CATEGORIES, ...categories])];
  return <Form heading="Add Score" submitLabel="Save Score" onCancel={onCancel} onDone={() => onDone("Score added.")}
    rules={[["category", "Category", !category], ["score", "Score", !score], ["period", "Period", !period], ["source", "Source order", !source.trim()], ["rationale", "Rationale", !rationale.trim()]]}
    send={() => outcome(() => apiClient.POST("/api/v1/vendors/{id}/scores", { params: { path: { id: vendorId } }, headers: mutationHeaders(crypto.randomUUID()),
      body: { category, score: Number(score), period: monthStart(period), source_ref: source.trim(), source_type: "po", rationale: rationale.trim() } }), "Adding the score")}>
    {(p, err) => <>
      <div className="perf-field"><label htmlFor={p("category").id}>Category</label>
        <select {...p("category")} value={category} onChange={e => setCategory(e.target.value)}><option value="">Choose a category</option>{options.map(c => <option key={c} value={c}>{c}</option>)}</select>{err("category")}</div>
      <div className="perf-field"><label htmlFor={p("score").id}>Score, 1 to 5</label>
        <select {...p("score")} value={score} onChange={e => setScore(e.target.value)}><option value="">Choose a score</option>{[1, 2, 3, 4, 5].map(n => <option key={n} value={n}>{n}</option>)}</select>{err("score")}</div>
      <div className="perf-field"><label htmlFor={p("period").id}>Period</label>
        <input type="month" {...p("period")} value={period} onChange={e => setPeriod(e.target.value)} />{err("period")}</div>
      <div className="perf-field"><label htmlFor={p("source").id}>Source order</label>
        <input {...p("source")} maxLength={200} value={source} onChange={e => setSource(e.target.value)} />{err("source")}</div>
      <div className="perf-field"><label htmlFor={p("rationale").id}>Rationale</label>
        <textarea {...p("rationale")} maxLength={1000} value={rationale} onChange={e => setRationale(e.target.value)} />{err("rationale")}</div>
    </>}
  </Form>;
}

export function DisputeForm({ vendorId, score, onDone, onCancel }: Common & { score: Score }) {
  const [reason, setReason] = useState("");
  return <Form heading={`Dispute ${score.source_ref} ${score.category_code}`} submitLabel="Raise Dispute" onCancel={onCancel} onDone={() => onDone("Dispute raised.")}
    rules={[["reason", "Reason", !reason.trim()]]}
    send={() => outcome(() => apiClient.POST("/api/v1/vendors/{id}/scores/{sid}/disputes", { params: { path: { id: vendorId, sid: score.id } }, headers: mutationHeaders(crypto.randomUUID()), body: { reason: reason.trim() } }), "Raising the dispute")}>
    {(p, err) => <div className="perf-field"><label htmlFor={p("reason").id}>Reason</label>
      <textarea {...p("reason")} maxLength={1000} value={reason} onChange={e => setReason(e.target.value)} />{err("reason")}</div>}
  </Form>;
}

export function ResolveForm({ vendorId, score, dispute, onDone, onCancel }: Common & { score: Score; dispute: Dispute }) {
  const [resolution, setResolution] = useState<"upheld" | "dismissed">("upheld");
  const [note, setNote] = useState(""), [adjusted, setAdjusted] = useState(""), [adjustedWhy, setAdjustedWhy] = useState("");
  const upheld = resolution === "upheld";
  return <Form heading={`Resolve dispute on ${score.source_ref} ${score.category_code}`} submitLabel="Resolve" onCancel={onCancel} onDone={() => onDone(upheld ? "Dispute upheld; the adjustment is added." : "Dispute dismissed; no figure changed.")}
    rules={[["note", "Note", !note.trim()], ["adjusted", "Adjusted score", upheld && !adjusted], ["why", "Adjusted rationale", upheld && !adjustedWhy.trim()]]}
    send={() => outcome(() => apiClient.POST("/api/v1/vendors/{id}/scores/{sid}/disputes/{did}/resolve", { params: { path: { id: vendorId, sid: score.id, did: dispute.id } }, headers: mutationHeaders(crypto.randomUUID()),
      body: upheld ? { resolution, note: note.trim(), adjusted_score: Number(adjusted), adjusted_rationale: adjustedWhy.trim() } : { resolution, note: note.trim() } }), "Resolving the dispute")}>
    {(p, err) => <>
      <p className="req-sub">Raised by {dispute.raised_by_email}: {dispute.reason}</p>
      <fieldset className="perf-field"><legend>Decision</legend>
        <label><input type="radio" name={`res-${dispute.id}`} checked={upheld} onChange={() => setResolution("upheld")} /> Upheld</label>
        <label><input type="radio" name={`res-${dispute.id}`} checked={!upheld} onChange={() => setResolution("dismissed")} /> Dismissed</label></fieldset>
      <div className="perf-field"><label htmlFor={p("note").id}>Note</label>
        <textarea {...p("note")} maxLength={1000} value={note} onChange={e => setNote(e.target.value)} />{err("note")}</div>
      {upheld && <>
        <div className="perf-field"><label htmlFor={p("adjusted").id}>Adjusted score, 1 to 5</label>
          <select {...p("adjusted")} value={adjusted} onChange={e => setAdjusted(e.target.value)}><option value="">Choose a score</option>{[1, 2, 3, 4, 5].map(n => <option key={n} value={n}>{n}</option>)}</select>{err("adjusted")}</div>
        <div className="perf-field"><label htmlFor={p("why").id}>Adjusted rationale</label>
          <textarea {...p("why")} maxLength={1000} value={adjustedWhy} onChange={e => setAdjustedWhy(e.target.value)} />{err("why")}</div>
      </>}
    </>}
  </Form>;
}
