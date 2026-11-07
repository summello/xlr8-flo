import { useRef, useState } from "react";
import { apiClient } from "../../../api/client";
import { failure, mutationHeaders, type Problem } from "../../requisitions/api";
import { cellKey, parseScore, type Evaluation } from "./api";

type Cell = { score: string; comment: string };
type Me = NonNullable<Evaluation["me"]>;
const fieldId = (key: string) => `eval-score-${key.replace("|", "-")}`;

/** One fold per vendor: a score 0..10 and a comment per criterion. The server decides what is editable, sealed or shown (SRC-011). */
export default function Scoring({ id, evaluation, me, onChanged }: { id: string; evaluation: Evaluation; me: Me; onChanged: (notice: string) => void }) {
  const mine = new Map(evaluation.scores.filter(s => s.identity_id === me.identity_id).map(s => [cellKey(s.vendor_id, s.criterion_id), s]));
  const [cells, setCells] = useState<Record<string, Cell>>(() => Object.fromEntries([...mine].map(([k, s]) => [k, { score: String(s.score), comment: s.comment ?? "" }])));
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const version = useRef(me.version), submitKey = useRef<string | null>(null);
  const summary = useRef<HTMLDivElement>(null);
  const all = evaluation.vendors.flatMap(v => evaluation.criteria.map(c => ({ vendor: v, criterion: c, key: cellKey(v.vendor_id, c.id ?? "") })));
  const editable = all.filter(c => mine.get(c.key)?.status !== "submitted");
  const set = (key: string, patch: Partial<Cell>) => setCells(s => ({ ...s, [key]: { score: "", comment: "", ...s[key], ...patch } }));

  const refuse = (found: Record<string, string>) => {
    setErrors(found);
    if (Object.keys(found).length) setTimeout(() => summary.current?.focus(), 0);
  };

  /** All or nothing: every problem is found before anything is sent. */
  const check = (every: boolean) => {
    const found: Record<string, string> = {};
    for (const c of editable) {
      const cell = cells[c.key], text = cell?.score.trim() ?? "";
      if (!text && !cell?.comment.trim() && !every) continue;
      if (parseScore(text) === null) found[c.key] = text ? "Enter a whole number from 0 to 10." : "Enter a score from 0 to 10.";
    }
    return found;
  };

  const serverRefusal = (p: Problem | undefined, status: number, sent: { vendor_id: string; criterion_id: string }[], action: string) => {
    submitKey.current = null;
    if (status !== 409 && status !== 422) { setMessage(failure(p, action)); return; }
    const found: Record<string, string> = {};
    for (const e of p?.errors ?? []) { const n = /^cells\/(\d+)$/.exec(e.field)?.[1], c = n === undefined ? undefined : sent[Number(n)]; if (c) found[cellKey(c.vendor_id, c.criterion_id)] = e.message; }
    if (Object.keys(found).length) refuse(found);
    else setMessage(failure(p, action));
  };

  const run = async (final: boolean) => {
    setMessage(null);
    const found = check(final);
    if (Object.keys(found).length) return refuse(found);
    setErrors({});
    const body = editable.filter(c => cells[c.key]?.score.trim()).map(c => ({ vendor_id: c.vendor.vendor_id, criterion_id: c.criterion.id ?? "", score: Number(cells[c.key]!.score), ...(cells[c.key]!.comment.trim() ? { comment: cells[c.key]!.comment.trim() } : {}) }));
    if (body.length === 0) { setMessage("Enter at least one score first."); return; }
    setBusy(true);
    try {
      const put = await apiClient.PUT("/api/v1/sourcing/rfqs/{id}/evaluation/scores", { params: { path: { id } }, headers: mutationHeaders(crypto.randomUUID(), version.current), body: { cells: body } });
      if (!put.data) return serverRefusal(put.error as Problem | undefined, put.response.status, body, final ? "Submitting your scores" : "Saving your scores");
      version.current = put.data.version;
      if (!final) return onChanged("Scores saved. You can finish later.");
      submitKey.current ??= crypto.randomUUID(); // one key per attempt, reused on retry
      const done = await apiClient.POST("/api/v1/sourcing/rfqs/{id}/evaluation/scores/submit", { params: { path: { id } }, headers: mutationHeaders(submitKey.current) });
      if (!done.data) return serverRefusal(done.error as Problem | undefined, done.response.status, body, "Submitting your scores");
      submitKey.current = null;
      onChanged("Scores submitted.");
    } catch {
      setMessage(failure(undefined, final ? "Submitting your scores" : "Saving your scores"));
    } finally { setBusy(false); }
  };

  const shown = editable.filter(c => errors[c.key]);
  const others = [...new Set(evaluation.scores.map(s => s.identity_id).filter(i => i !== me.identity_id))];
  const weight = (vendorId: string) => evaluation.weighted?.find(w => w.vendor_id === vendorId)?.score;
  return <div className="eval-scoring">
    <p className="req-sub">Score every criterion from 0 to 10. Weights are fixed by the RFQ. Nothing is final until you choose Submit Scores.</p>
    {shown.length > 0 && <div ref={summary} role="alert" tabIndex={-1} className="eval-summary"><h2>Check {shown.length} {shown.length === 1 ? "score" : "scores"}</h2>
      <ul>{shown.map(c => <li key={c.key}><a href={`#${fieldId(c.key)}`} onClick={e => { e.preventDefault(); document.getElementById(fieldId(c.key))?.focus(); }}>{c.vendor.vendor_name}, {c.criterion.name}: {errors[c.key]}</a></li>)}</ul></div>}
    {evaluation.vendors.map(v => <details key={v.vendor_id} className="eval-fold" open>
      <summary>{v.vendor_name}</summary>
      <table className="vendor-table">
        <caption className="visually-hidden">Scores for {v.vendor_name}</caption>
        <thead><tr><th scope="col">Criterion</th><th scope="col">Weight</th><th scope="col">Score (0 to 10)</th><th scope="col">Comment</th></tr></thead>
        <tbody>{evaluation.criteria.map(c => {
          const key = cellKey(v.vendor_id, c.id ?? ""), held = mine.get(key), cell = cells[key];
          return <tr key={key}>
            <th scope="row" data-label="Criterion">{c.name}</th>
            <td data-label="Weight">{c.weight}%</td>
            {held?.status === "submitted" ? <><td data-label="Score (0 to 10)">{held.score} <span className="req-sub">Submitted</span></td><td data-label="Comment">{held.comment ?? "No comment"}</td></> : <>
              <td data-label="Score (0 to 10)" data-invalid={errors[key] ? "true" : undefined}>
                <input id={fieldId(key)} type="text" inputMode="numeric" aria-label={`Score for ${c.name}, ${v.vendor_name}, 0 to 10`} value={cell?.score ?? ""} onChange={e => set(key, { score: e.target.value })} aria-invalid={errors[key] ? true : undefined} aria-describedby={errors[key] ? `${fieldId(key)}-error` : undefined} />
                {errors[key] && <p role="alert" id={`${fieldId(key)}-error`} className="field-error">{errors[key]}</p>}
              </td>
              <td data-label="Comment"><textarea rows={2} maxLength={2000} aria-label={`Comment for ${c.name}, ${v.vendor_name}`} value={cell?.comment ?? ""} onChange={e => set(key, { comment: e.target.value })} /></td></>}
          </tr>;
        })}</tbody>
      </table>
    </details>)}
    {message && <p role="alert" className="field-error">{message} Your entries are kept.</p>}
    {editable.length > 0 && <div className="req-actions">
      <button type="button" disabled={busy} onClick={() => void run(false)}>Save for Later</button>
      <button type="button" disabled={busy} aria-busy={busy || undefined} onClick={() => void run(true)}>{busy ? "Working…" : "Submit Scores"}</button>
    </div>}
    {others.length > 0 && <section aria-labelledby="eval-all" className="eval-all">
      <h2 id="eval-all">Everyone's Scores</h2>
      {evaluation.vendors.map(v => <table className="vendor-table" key={v.vendor_id}>
        <caption>{v.vendor_name}{weight(v.vendor_id) ? `, weighted score ${weight(v.vendor_id)}` : ""}</caption>
        <thead><tr><th scope="col">Criterion</th><th scope="col">You</th>{others.map((o, n) => <th scope="col" key={o}>Evaluator {n + 2}</th>)}</tr></thead>
        <tbody>{evaluation.criteria.map(c => <tr key={c.id}>
          <th scope="row" data-label="Criterion">{c.name}</th>
          {[me.identity_id, ...others].map((who, n) => { const s = evaluation.scores.find(x => x.identity_id === who && x.vendor_id === v.vendor_id && x.criterion_id === c.id); return <td key={who} data-label={n === 0 ? "You" : `Evaluator ${n + 1}`}>{s ? <>{s.score}{s.comment && <span className="req-sub">{s.comment}</span>}</> : "None"}</td>; })}
        </tr>)}</tbody>
      </table>)}
    </section>}
  </div>;
}
