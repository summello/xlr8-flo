import { ArrowDown, ArrowUp, FloppyDisk, Plus, Trash } from "@phosphor-icons/react";
import { useState } from "react";
import { apiClient } from "../../../api/client";
import { failure, mutationHeaders, type Problem } from "../../requisitions/api";
import { useLoad } from "../../requisitions/useLoad";
import type { CardProps } from "./cards";

type Row = { name: string; weight: string };
const MAX_ROWS = 20;
const swap = (rows: Row[], a: number, b: number) => rows.map((r, i) => (i === a ? rows[b]! : i === b ? rows[a]! : r));

/** Draft: edit name and weight, add, remove, reorder with buttons. Amend: read-only. The page shows no weight total; the server's `weights_total` at publish is the signal. */
export default function CriteriaCard({ rfq, mode, onRfq, report }: CardProps) {
  const [rows, setRows] = useState<Row[] | null>(null);
  const [source, setSource] = useState<"default" | "rfq">("rfq");
  const [errors, setErrors] = useState<Record<number, string>>({});
  const [moved, setMoved] = useState("");
  const [saved, setSaved] = useState(false);
  const [busy, setBusy] = useState(false);
  const load = useLoad(async () => {
    const { data, error } = await apiClient.GET("/api/v1/sourcing/rfqs/{id}/evaluation", { params: { path: { id: rfq.id } } });
    if (!data) throw new Error(failure(error as Problem | undefined, "Loading the evaluation criteria"));
    setRows(data.criteria.map(c => ({ name: c.name, weight: String(c.weight) }))); setSource(data.source);
    return data;
  }, [rfq.id]);

  const head = <header className="req-card-head"><h2 id="rfq-card-criteria">Evaluation Criteria</h2></header>;
  if (load.error) return <section className="req-card" aria-labelledby="rfq-card-criteria">{head}<div role="alert"><p>{load.error}</p><button type="button" onClick={load.reload}>Try again</button></div></section>;
  if (!load.data || !rows) return <section className="req-card req-card-skeleton" aria-labelledby="rfq-card-criteria" aria-busy="true">{head}<p className="req-skeleton" role="status" aria-label="Loading evaluation criteria" /></section>;

  if (mode === "amend") return <section className="req-card" aria-labelledby="rfq-card-criteria">{head}
    <p className="req-sub">Criteria cannot change after publication.</p>
    {rows.length === 0 ? <p>No criteria yet.</p> : <ul className="vendor-list-plain">{rows.map((r, n) => <li key={n}>{r.name}<span className="req-sub">Weight {r.weight}</span></li>)}</ul>}
  </section>;

  const edit = (n: number, change: Partial<Row>) => { setSaved(false); setRows(rows.map((r, i) => (i === n ? { ...r, ...change } : r))); };
  const move = (n: number, to: number) => { setSaved(false); setRows(swap(rows, n, to)); setMoved(`${rows[n]!.name || `Criterion ${n + 1}`} moved to position ${to + 1} of ${rows.length}.`); };

  const save = async () => {
    const found: Record<number, string> = {};
    rows.forEach((r, n) => {
      const weight = Number(r.weight);
      if (r.name.trim() === "" || r.name.trim().length > 120) found[n] = "Enter a name of 1 to 120 characters.";
      else if (!/^\d+$/.test(r.weight.trim()) || weight < 1 || weight > 100) found[n] = "Enter a whole-number weight from 1 to 100.";
    });
    setErrors(found);
    if (rows.length === 0) { report("Add at least one criterion."); return; }
    if (Object.keys(found).length) { report("Fix the highlighted criteria before saving."); return; }
    setBusy(true); report(null);
    try {
      const { data, error } = await apiClient.PUT("/api/v1/sourcing/rfqs/{id}/evaluation/criteria", { params: { path: { id: rfq.id } }, headers: mutationHeaders(crypto.randomUUID(), rfq.edit_revision), body: { criteria: rows.map(r => ({ name: r.name.trim(), weight: Number(r.weight) })) } });
      if (!data) { report((error as Problem | undefined)?.errors?.[0]?.message ?? failure(error as Problem | undefined, "Saving the criteria")); return; }
      setRows(data.criteria.map(c => ({ name: c.name, weight: String(c.weight) }))); setSource(data.source); setSaved(true);
      onRfq({ ...rfq, edit_revision: data.edit_revision });
    } catch { report(failure(undefined, "Saving the criteria")); } finally { setBusy(false); }
  };

  return <section className="req-card" aria-labelledby="rfq-card-criteria" aria-busy={busy || undefined}>{head}
    {source === "default" && <p className="req-sub">From your organisation's default. Save to keep them for this RFQ.</p>}
    {rows.length === 0 ? <p>No criteria yet.</p> : <ul className="vendor-list-plain">{rows.map((r, n) => <li key={n} className="rfq-invitee">
      <div className="form-field"><label htmlFor={`rfq-criterion-name-${n}`}>Criterion {n + 1} Name</label>
        <input id={`rfq-criterion-name-${n}`} value={r.name} maxLength={120} aria-invalid={errors[n] ? true : undefined} aria-describedby={errors[n] ? `rfq-criterion-error-${n}` : undefined} onChange={e => edit(n, { name: e.target.value })} /></div>
      <div className="form-field"><label htmlFor={`rfq-criterion-weight-${n}`}>Criterion {n + 1} Weight</label>
        <input id={`rfq-criterion-weight-${n}`} inputMode="numeric" value={r.weight} aria-invalid={errors[n] ? true : undefined} aria-describedby={errors[n] ? `rfq-criterion-error-${n}` : undefined} onChange={e => edit(n, { weight: e.target.value })} /></div>
      {errors[n] && <p id={`rfq-criterion-error-${n}`} role="alert" className="req-sub">{errors[n]}</p>}
      <div className="req-actions">
        <button type="button" aria-label={`Move criterion ${n + 1} up`} disabled={n === 0} onClick={() => move(n, n - 1)}><ArrowUp aria-hidden="true" weight="regular" />Move Up</button>
        <button type="button" aria-label={`Move criterion ${n + 1} down`} disabled={n === rows.length - 1} onClick={() => move(n, n + 1)}><ArrowDown aria-hidden="true" weight="regular" />Move Down</button>
        <button type="button" aria-label={`Remove criterion ${n + 1}`} onClick={() => { setSaved(false); setRows(rows.filter((_, i) => i !== n)); }}><Trash aria-hidden="true" weight="regular" />Remove</button>
      </div>
    </li>)}</ul>}
    <p className="visually-hidden" role="status">{moved}</p>
    <div className="req-actions">
      <button type="button" disabled={rows.length >= MAX_ROWS} onClick={() => { setSaved(false); setRows([...rows, { name: "", weight: "" }]); }}><Plus aria-hidden="true" weight="regular" />Add Criterion</button>
      <button type="button" aria-busy={busy || undefined} onClick={() => void save()}><FloppyDisk aria-hidden="true" weight="regular" />Save Criteria</button>
    </div>
    {saved && <p role="status">Criteria saved.</p>}
  </section>;
}
