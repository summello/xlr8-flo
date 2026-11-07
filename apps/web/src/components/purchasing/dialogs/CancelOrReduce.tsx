import * as Dialog from "@radix-ui/react-dialog";
import { Prohibit, X } from "@phosphor-icons/react";
import { useCallback, useEffect, useId, useRef, useState } from "react";
import { apiClient } from "../../../api/client";
import Money from "../../ui/Money";
import { failure, mutationHeaders, type Problem } from "../../requisitions/api";
import { cancelConsequence, type Po, type PoCancelPreview, type PoChange } from "../api";
import "../../../styles/vendors.css";
import "../../../styles/projects.css";

type Row = { qty: string; remove: boolean };
type Mode = "cancel" | "reduce";
type Refusal = { message: string; stale: boolean };

const QUANTITY = /^[0-9]{1,14}(\.[0-9]{1,4})?$/;
/** Quantities carry four decimals; compare them as integers, never as floats. */
const scaled = (v: string) => { const [i = "0", f = ""] = v.split("."); return BigInt(i + f.padEnd(4, "0").slice(0, 4)); };
const plain = (v: string) => (v.includes(".") ? v.replace(/\.?0+$/, "") : v);

/** pages/purchase-orders.md 2 and MASTER 7.13: cancel the whole PO or reduce quantities by change order. The figures are the server's; the browser only words them. */
export default function CancelOrReduce({ po, onDone }: { po: Po; onDone: () => void }) {
  const actions = po.actions ?? [], canReduce = actions.includes("reduce"), issued = po.status === "issued";
  const [open, setOpen] = useState(false);
  const [mode, setMode] = useState<Mode>("cancel");
  const [preview, setPreview] = useState<{ data?: PoCancelPreview; error?: string }>({});
  const [alsoLines, setAlsoLines] = useState(false), [notify, setNotify] = useState(true);
  const [rows, setRows] = useState<Record<number, Row>>({});
  const [rowErrors, setRowErrors] = useState<Record<number, string>>({});
  const [reason, setReason] = useState(""), [fieldError, setFieldError] = useState<string | null>(null);
  const [refusal, setRefusal] = useState<Refusal | null>(null), [busy, setBusy] = useState(false);
  const [result, setResult] = useState<PoChange | null>(null);
  const key = useRef<string | null>(null);
  const safe = useRef<HTMLButtonElement>(null), opener = useRef<HTMLButtonElement>(null), field = useRef<HTMLTextAreaElement>(null), done = useRef<HTMLButtonElement>(null);
  const uid = useId();

  const loadPreview = useCallback(async () => {
    setPreview({});
    const { data, error } = await apiClient.GET("/api/v1/purchase-orders/{id}/cancel-preview", { params: { path: { id: po.id } } });
    setPreview(data ? { data } : { error: failure(error as Problem | undefined, "Loading what cancelling would do") });
  }, [po.id]);
  useEffect(() => { if (result) done.current?.focus(); }, [result]);

  const reset = () => { setMode("cancel"); setAlsoLines(false); setNotify(true); setRows({}); setRowErrors({}); setReason(""); setFieldError(null); setRefusal(null); setResult(null); key.current = null; };
  const finish = () => { setOpen(false); onDone(); };

  const send = async (attempt: (k: string) => Promise<{ ok: boolean; problem?: Problem; status: number; change?: PoChange }>, label: string) => {
    setFieldError(null); setRefusal(null); setBusy(true);
    key.current ??= crypto.randomUUID(); // one key per attempt, reused on a network retry
    try {
      const r = await attempt(key.current);
      if (r.ok) { key.current = null; if (r.change) setResult(r.change); else finish(); return; }
      const final = r.status === 409 || r.status === 403 || r.status === 422;
      if (final) key.current = null;
      setRefusal({ message: failure(r.problem, label), stale: r.status === 409 && (r.problem as { checks?: { problem?: string } } | undefined)?.checks?.problem === "stale_version" });
    } catch {
      setRefusal({ message: `${label} failed. Your entries are kept. Check your connection and try again.`, stale: false });
    } finally { setBusy(false); }
  };

  const cancelPo = () => {
    if (!reason.trim()) { setFieldError("Give a reason."); field.current?.focus(); return; }
    void send(async k => {
      const { response, error } = await apiClient.POST("/api/v1/purchase-orders/{id}/cancel", { params: { path: { id: po.id } }, headers: mutationHeaders(k, po.version),
        body: { reason: reason.trim(), cancel_requisition_lines: issued && alsoLines, notify_vendor: issued ? notify : true } });
      return { ok: response.ok, problem: error as Problem | undefined, status: response.status };
    }, "Cancelling");
  };

  const reduce = () => {
    const errors: Record<number, string> = {}, edits: ({ line_no: number; quantity: string } | { line_no: number; remove: true })[] = [];
    for (const l of po.lines) {
      const r = rows[l.line_no];
      if (r?.remove) { edits.push({ line_no: l.line_no, remove: true }); continue; }
      const q = r?.qty.trim();
      if (!q) continue;
      if (!QUANTITY.test(q) || scaled(q) <= 0n || scaled(q) >= scaled(l.quantity)) errors[l.line_no] = `Enter a quantity between 0 and ${plain(l.quantity)}; to drop the line, choose Remove.`;
      else edits.push({ line_no: l.line_no, quantity: q });
    }
    setRowErrors(errors);
    if (Object.keys(errors).length) return;
    if (!edits.length) { setFieldError("Lower a quantity or remove a line."); return; }
    if (!reason.trim()) { setFieldError("Give a reason."); field.current?.focus(); return; }
    void send(async k => {
      const { data, response, error } = await apiClient.POST("/api/v1/purchase-orders/{id}/reduce", { params: { path: { id: po.id } }, headers: mutationHeaders(k, po.version), body: { reason: reason.trim(), lines: edits } });
      return { ok: response.ok, problem: error as Problem | undefined, status: response.status, change: data };
    }, "Submitting the reduction");
  };

  const edit = (n: number, patch: Partial<Row>) => setRows(s => ({ ...s, [n]: { qty: "", remove: false, ...s[n], ...patch } }));
  const trigger = canReduce ? "Cancel or Reduce" : "Cancel PO";
  if (!actions.includes("cancel")) return null;
  return <Dialog.Root open={open} onOpenChange={next => { if (busy && !next) return; setOpen(next); }}>
    <button type="button" ref={opener} className="vendor-destructive req-destructive" onClick={() => { reset(); setOpen(true); void loadPreview(); }}><Prohibit aria-hidden="true" weight="regular" />{trigger}</button>
    <Dialog.Portal><Dialog.Overlay className="project-dialog-overlay" />
      <Dialog.Content className="project-dialog material-surface material-shadow req-dialog vendor-dialog" data-shadow="lg"
        onOpenAutoFocus={e => { e.preventDefault(); safe.current?.focus(); }}
        onCloseAutoFocus={e => { e.preventDefault(); opener.current?.focus(); }}
        onEscapeKeyDown={e => { if (busy) e.preventDefault(); }} onInteractOutside={e => { if (busy) e.preventDefault(); }}>
        {result ? <>
          <Dialog.Title>Change Order {result.number} Submitted</Dialog.Title>
          <Dialog.Description>It is waiting for approval.</Dialog.Description>
          <ul className="vendor-list-plain" aria-label="Budget effect">{result.budget_effect.map((e, i) => <li key={i}>
            <Money value={String(e.delta)} currency={String(e.currency)} zero="value" /> · {String(e.department_code)} · {String(e.ledger_account_code)}</li>)}</ul>
          <div className="req-actions"><button type="button" ref={done} onClick={finish}>Close</button></div>
        </> : <>
          <Dialog.Title>{mode === "cancel" ? `Cancel ${po.number}` : `Reduce ${po.number}`}</Dialog.Title>
          <Dialog.Description>{mode === "cancel" ? "The order is closed to further changes." : "Lowering quantities raises a change order for approval. When it is approved, the unused commitment of the removed quantity is released once, re-reserved for the requisition while it holds a reservation (DECISIONS 209), and the new revision is sent to the vendor."}</Dialog.Description>
          {canReduce && <fieldset className="vendor-dialog-units"><legend className="t-label">What to do</legend>
            {([["cancel", "Cancel the Whole PO"], ["reduce", "Reduce Quantities"]] as const).map(([value, text]) =>
              <label key={value} className="project-checkbox"><input type="radio" name={`${uid}-mode`} checked={mode === value} onChange={() => { setMode(value); setRefusal(null); setFieldError(null); key.current = null; }} />{text}</label>)}
          </fieldset>}
          {mode === "cancel" ? <>
            <ul className="vendor-list-plain" aria-label="What happens">
              <li>{preview.data ? <><strong>Budget.</strong> {cancelConsequence(preview.data, alsoLines)}</> : preview.error
                ? <><span role="alert">{preview.error}</span> <button type="button" onClick={() => void loadPreview()}>Try Again</button></>
                : <span className="req-skeleton" role="status" aria-label="Loading what happens" />}</li>
              <li>The PO, its lines, versions and history stay readable.</li>
              <li>{issued ? (notify ? "The vendor is told this PO is cancelled." : "The vendor is not told.") : "The vendor never received this PO, so nothing is sent."}</li>
            </ul>
            {issued && <>
              <label className="project-checkbox"><input type="checkbox" checked={alsoLines} onChange={e => setAlsoLines(e.target.checked)} />Also cancel these requisition lines</label>
              <label className="project-checkbox"><input type="checkbox" checked={notify} onChange={e => setNotify(e.target.checked)} />Tell the vendor this PO is cancelled</label>
            </>}
          </> : <table className="po-lines"><thead><tr><th scope="col">Line</th><th scope="col">Description</th><th scope="col" data-numeric>Quantity</th><th scope="col">New Quantity</th><th scope="col">Remove</th></tr></thead>
            <tbody>{po.lines.map(l => { const r = rows[l.line_no], err = rowErrors[l.line_no]; return <tr key={l.line_no}>
              <td>{l.line_no}</td><td>{l.description}</td><td data-numeric>{plain(l.quantity)} {l.uom_code}</td>
              <td><input type="text" inputMode="decimal" aria-label={`New quantity for line ${l.line_no}`} value={r?.qty ?? ""} disabled={r?.remove} aria-invalid={err ? true : undefined} aria-describedby={err ? `${uid}-q${l.line_no}` : undefined} onChange={e => edit(l.line_no, { qty: e.target.value })} />
                {err && <p role="alert" id={`${uid}-q${l.line_no}`} className="field-error">{err}</p>}</td>
              <td><input type="checkbox" aria-label={`Remove line ${l.line_no}`} checked={r?.remove ?? false} onChange={e => edit(l.line_no, { remove: e.target.checked })} /></td></tr>; })}</tbody></table>}
          <div className="req-field" data-invalid={fieldError ? true : undefined}><label className="t-label" htmlFor={`${uid}-reason`}>Reason (required)</label>
            <textarea id={`${uid}-reason`} ref={field} rows={3} maxLength={500} value={reason} onChange={e => setReason(e.target.value)} aria-invalid={fieldError ? true : undefined} aria-describedby={fieldError ? `${uid}-error` : undefined} />
            {fieldError && <p role="alert" id={`${uid}-error`} className="field-error">{fieldError}</p>}</div>
          {refusal && <p role="alert" className="field-error">{refusal.message} Your reason is kept.{refusal.stale && <> <button type="button" onClick={finish}>Reload</button></>}</p>}
          <div className="req-actions">
            <button type="button" ref={safe} onClick={() => setOpen(false)} disabled={busy}>Keep PO</button>
            <button type="button" className="vendor-danger-confirm" onClick={mode === "cancel" ? cancelPo : reduce} aria-busy={busy || undefined} disabled={busy}>
              {mode === "cancel" ? (busy ? "Cancelling…" : "Cancel PO") : busy ? "Submitting…" : "Submit Reduction"}</button>
          </div>
        </>}
        <Dialog.Close aria-label="Close dialog" className="grid-sheet-close" disabled={busy}><X aria-hidden="true" weight="regular" /></Dialog.Close>
      </Dialog.Content>
    </Dialog.Portal>
  </Dialog.Root>;
}
