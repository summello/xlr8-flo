import { useCallback, useEffect, useRef, useState } from "react";
import { apiClient } from "../../api/client";
import { writeHeaders } from "../../features/auth/api";
import { registerGuard } from "../../lib/navigation-guard";
import ErrorSummary, { type SummaryError } from "../../components/form/ErrorSummary";
import Field from "../../components/form/Field";
import Input from "../../components/form/Input";
import MoneyInput from "../../components/form/MoneyInput";
import { useFieldContext } from "../../components/form/context";
import StatusPill from "../../components/status/StatusPill";
import Money from "../../components/ui/Money";
import type { Po, PoBudget, PoChange } from "../../components/purchasing/api";
import { ApprovalLine, DiffCard, EffectCard } from "../../components/purchasing/changes/cards";
import { COLUMNS, changeBody, initialEdited, type Column, type Edited } from "../../components/purchasing/changes/body";
import { failure, label, type Problem } from "../../components/requisitions/api";
import { useLoad } from "../../components/requisitions/useLoad";
import "../../styles/projects.css";
import "../../styles/requisitions.css";
import "../../styles/vendors.css";
import "../../styles/sourcing.css";
import "../../styles/purchase-orders.css";

const MISSING = "This change order does not exist or you cannot see it.";
const NEEDS = "This change now needs approval. Review it and press Submit for Approval.";
const NOT_NEEDED = "This change no longer needs approval. Review it and press Apply.";
const COLUMN_LABEL: Record<Column, string> = { quantity: "Quantity", unit_price: "Unit price", tax: "Tax", freight: "Freight" };

function Area({ value, onChange, maxLength }: { value: string; onChange: (v: string) => void; maxLength: number }) {
  const f = useFieldContext();
  return <textarea id={f.id} rows={3} maxLength={maxLength} value={value} aria-describedby={f.describedBy} aria-invalid={f.error === undefined ? undefined : true} onChange={e => onChange(e.target.value)} />;
}

export default function PurchaseOrderChange({ id, number, navigate }: { id: string; number: number; navigate: (href: string) => void }) {
  const [gen, setGen] = useState(0);
  const [notice, setNotice] = useState<string | null>(null);
  const load = useLoad(async () => {
    const c = await apiClient.GET("/api/v1/purchase-orders/{id}/changes/{number}", { params: { path: { id, number } } });
    if (!c.data) throw new Error(c.response?.status === 404 ? MISSING : failure(c.error as Problem | undefined, "Loading the change order"));
    const [p, b] = await Promise.all([
      apiClient.GET("/api/v1/purchase-orders/{id}", { params: { path: { id: c.data.po_id } } }),
      apiClient.GET("/api/v1/purchase-orders/{id}/budget-summary", { params: { path: { id: c.data.po_id } } }).catch(() => undefined),
    ]);
    if (!p.data) throw new Error(p.response?.status === 404 ? MISSING : failure(p.error as Problem | undefined, "Loading the purchase order"));
    return { change: c.data, po: p.data, projects: b?.data?.projects ?? [], gen };
  }, [id, number, gen]);
  const reload = useCallback((message?: string) => { setNotice(message ?? null); setGen(g => g + 1); }, []);
  useEffect(() => { document.title = `Change order ${number}`; }, [number]);
  const back = (e: React.MouseEvent) => { e.preventDefault(); navigate(`/purchase-orders/${id}`); };

  if (load.error && !load.data) return <section className="req-page" aria-label="Change order"><div role="alert"><p>{load.error}</p><button type="button" onClick={() => reload()}>Try again</button></div><a href={`/purchase-orders/${id}`} onClick={back}>Back to the purchase order</a></section>;
  if (!load.data) return <section className="req-page" aria-label="Change order"><div className="req-card req-card-skeleton" aria-busy="true"><p className="req-skeleton" role="status" aria-label="Loading change order" /></div></section>;
  const { change, po, projects } = load.data;
  return <Editor key={load.data.gen} id={id} number={number} change={change} po={po} projects={projects} notice={notice} reload={reload} navigate={navigate} />;
}

function Editor({ id, number, change: first, po, projects, notice, reload, navigate }: {
  id: string; number: number; change: PoChange; po: Po; projects: PoBudget["projects"]; notice: string | null;
  reload: (message?: string) => void; navigate: (href: string) => void;
}) {
  const [change, setChange] = useState(first);
  const [edited, setEdited] = useState(() => initialEdited(po, first));
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [banner, setBanner] = useState<string | null>(notice);
  const [stale, setStale] = useState(false);
  const [updating, setUpdating] = useState(false);
  const [rejected, setRejected] = useState(false);
  const [busy, setBusy] = useState(false);
  const editedRef = useRef(edited);
  const changeRef = useRef(first);
  const sent = useRef(JSON.stringify(changeBody(po, edited)));
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const running = useRef<Promise<boolean> | null>(null);
  const editable = change.status === "draft" && (po.actions ?? []).includes("change_order");
  const path = { id, number };

  useEffect(() => () => clearTimeout(timer.current), []);
  useEffect(() => {
    if (!editable) return;
    // Unsent: typed but not yet accepted by the server (pending, in flight or failed).
    return registerGuard(() => JSON.stringify(changeBody(po, editedRef.current)) !== sent.current);
  }, [editable, po]);

  const project = (projectId: string) => { const p = projects.find(x => x.project_id === projectId); return p ? `${p.project_code} ${p.project_name}` : "Project"; };

  /** Map a failed write onto the screen. Typed values are never touched. */
  const explain = (status: number | undefined, p: Problem | undefined, action: string, body?: ReturnType<typeof changeBody>) => {
    const problem = p?.checks?.problem;
    if (status === 422 && problem !== "no_changes") {
      const next: Record<string, string> = {};
      for (const e of p?.errors ?? []) {
        const line = /^lines\/(\d+)\/(\w+)$/.exec(e.field) ?? /^lines\[(\d+)\]\.(\w+)$/.exec(e.field);
        const n = line ? (e.field.includes("/") ? Number(line[1]) : body?.lines?.[Number(line[1])]?.line_no) : undefined;
        const key = n !== undefined && line ? `chg-l${n}-${line[2]}` : ({ terms: "chg-terms", needed_by: "chg-needed", reason: "chg-reason", document_freight: "chg-docfreight" } as Record<string, string>)[e.field] ?? "chg-reason";
        next[key] = e.message;
      }
      setErrors(next); setRejected(true);
      if (Object.keys(next).length === 0) setBanner(failure(p, action));
    } else if (problem === "stale_version") setStale(true);
    else if (problem === "invalid_transition") reload();
    else if (problem === "approval_required") reload(NEEDS);
    else if (problem === "not_material") reload(NOT_NEEDED);
    else if (problem === "no_changes") setBanner("This change order changes nothing yet.");
    else if (problem === "workflow_not_set_up") setBanner("No purchase order change workflow is published. Ask an administrator to set one up. Your change order is kept as a draft.");
    else if (problem === "over_open_quantity") {
      const next: Record<string, string> = {};
      for (const l of po.lines) { const open = p?.checks?.[`open.${l.rfq_line_id}`]; if (open) next[`chg-l${l.line_no}-quantity`] = `Only ${open} is still open to order on the award.`; }
      setErrors(next);
    } else if (status === 403 && p?.detail) setBanner(p.detail);
    else setBanner(failure(p, action));
  };

  /** One PATCH in flight at a time; after it returns, send again if the form moved on meanwhile. */
  const pump = (): Promise<boolean> => {
    clearTimeout(timer.current); timer.current = undefined;
    if (running.current) return running.current;
    running.current = (async () => {
      try {
        for (;;) {
          const body = changeBody(po, editedRef.current), key = JSON.stringify(body);
          if (key === sent.current) return true;
          setUpdating(true);
          const { data, error, response } = await apiClient.PATCH("/api/v1/purchase-orders/{id}/changes/{number}", {
            params: { path }, headers: { ...writeHeaders(), "If-Match": String(changeRef.current.version) }, body,
          }).catch(() => ({ data: undefined, error: undefined, response: undefined }));
          if (!data) { setErrors({}); setBanner(null); explain(response?.status, error as Problem | undefined, "Saving the change order", body); return false; }
          sent.current = key; changeRef.current = data; setChange(data); setErrors({}); setBanner(null); setRejected(false);
        }
      } finally { running.current = null; setUpdating(timer.current !== undefined); }
    })();
    return running.current;
  };

  const edit = (next: (e: Edited) => Edited) => {
    editedRef.current = next(editedRef.current); setEdited(editedRef.current);
    setUpdating(true); clearTimeout(timer.current);
    // A request delay while typing, not a motion value.
    timer.current = setTimeout(() => void pump(), 400);
  };
  const setLine = (n: number, c: Column, v: string) => edit(e => ({ ...e, lines: { ...e.lines, [n]: { ...e.lines[n]!, [c]: v } } }));

  const act = async (kind: "apply" | "submit") => {
    if (busy) return;
    setBusy(true); setBanner(null);
    try {
      if (!(await pump())) return;
      if (changeRef.current.material !== (kind === "submit")) { setBanner(kind === "submit" ? NOT_NEEDED : NEEDS); return; }
      const { data, error, response } = await apiClient.POST(`/api/v1/purchase-orders/{id}/changes/{number}/${kind}`, {
        params: { path }, headers: { ...writeHeaders(), "If-Match": String(changeRef.current.version) },
      }).catch(() => ({ data: undefined, error: undefined, response: undefined }));
      if (!data) { explain(response?.status, error as Problem | undefined, kind === "apply" ? "Applying the change order" : "Submitting the change order"); return; }
      if (kind === "apply" && data.result_po_id) navigate(`/purchase-orders/${data.result_po_id}`);
      else { changeRef.current = data; setChange(data); }
    } finally { setBusy(false); }
  };

  const summary: SummaryError[] = Object.entries(errors).map(([fieldId, message]) => ({ fieldId, message, label: fieldId.startsWith("chg-l") ? `Line ${fieldId.slice(5).replace("-", " ")}`.replace("unit_price", "unit price") : ({ "chg-terms": "Terms", "chg-needed": "Needed by", "chg-reason": "Reason", "chg-docfreight": "Document freight" } as Record<string, string>)[fieldId] ?? "Change order" }));
  const readOnly = change.status === "draft" ? "You can view this change order. Changing it needs purchase order amend access."
    : change.status === "approval_pending" ? "Sent for approval. The issued PO is unchanged until the change is approved."
    : change.status === "approved" ? null : `This change order was ${change.status}. The issued PO is unchanged.`;
  const v = po.revision + 1;
  const go = (href: string) => (e: React.MouseEvent) => { e.preventDefault(); navigate(href); };
  const doc = po.freight_mode === "document";

  return <section className="req-page vendor-profile po-page" aria-label="Change order">
    <header className="vendor-head">
      <div><h2 className="vendor-name po-number">{po.number} · Change order {number}</h2>
        <p className="req-sub">{label(change.status)} · Changes v{v} · <a href={`/purchase-orders/${po.id}`} onClick={go(`/purchase-orders/${po.id}`)}>View v{v}</a></p></div>
      <StatusPill docType="purchase_order" status={po.status as "draft"} />
    </header>
    {!editable && <p role="status">{readOnly ?? <>Applied as v{v + 1}. {change.result_po_id && <a href={`/purchase-orders/${change.result_po_id}`} onClick={go(`/purchase-orders/${change.result_po_id}`)}>Open v{v + 1}</a>}</>}</p>}
    {banner && <p role="alert">{banner}</p>}
    {stale && <div role="alert"><p>Someone changed this change order after you opened it. Your edits are still here; reload to see theirs (reloading discards yours).</p><button type="button" onClick={() => reload()}>Reload</button></div>}
    <ErrorSummary errors={summary} />
    <div className="po-body">
      <div className="po-col">
        <section className="req-card" aria-labelledby="chg-form-h">
          <header className="req-card-head"><h2 id="chg-form-h">Changes</h2></header>
          {editable ? <>
            <Field id="chg-reason" label="Reason" help="Shown with the change order in the version list." error={errors["chg-reason"]}>
              <Area value={edited.reason} maxLength={500} onChange={t => edit(e => ({ ...e, reason: t }))} />
            </Field>
            <Field id="chg-needed" label="Needed by" help="Leave empty to clear it." error={errors["chg-needed"]}>
              <Input type="date" autoComplete="off" inputMode="text" value={edited.neededBy} onChange={e => edit(s => ({ ...s, neededBy: e.target.value }))} />
            </Field>
            <Field id="chg-terms" label="Terms" help="The terms printed on the purchase order." error={errors["chg-terms"]}>
              <Area value={edited.terms} maxLength={4000} onChange={t => edit(e => ({ ...e, terms: t }))} />
            </Field>
            {doc && <Field id="chg-docfreight" label="Document freight" help="Freight for the whole order." error={errors["chg-docfreight"]}>
              <MoneyInput currency={po.currency} autoComplete="off" value={edited.documentFreight} onChange={e => edit(s => ({ ...s, documentFreight: e.target.value, freightSet: s.freightSet || e.target.value.trim() !== "" }))} />
            </Field>}
          </> : <dl className="project-facts">
            <div><dt>Reason</dt><dd>{change.reason ?? "—"}</dd></div>
          </dl>}
          <table className="po-lines po-edit-lines">
            <caption className="visually-hidden">Lines of v{v}</caption>
            <thead><tr><th scope="col">Line</th>{COLUMNS.map(c => <th key={c} scope="col" data-numeric>{COLUMN_LABEL[c]}{c === "unit_price" && <span className="req-sub"> A price can only go down.</span>}</th>)}<th scope="col" data-numeric>Line total</th></tr></thead>
            <tbody>{po.lines.map(l => {
              const row = edited.lines[l.line_no]!;
              return <tr key={l.line_no}>
                <th scope="row">{l.description} <span className="req-sub">{l.uom_code}</span>
                  <span className="req-sub">{project(l.project_id)} · {l.department_code ?? "—"} · {l.ledger_account_code ?? "—"}</span></th>
                {COLUMNS.map(c => <td key={c} data-numeric>
                  {!editable || (c === "freight" && doc) ? (c === "quantity" ? row[c] : <Money zero="value" value={row[c]} currency={po.currency} />) :
                    <Field id={`chg-l${l.line_no}-${c}`} label={`${COLUMN_LABEL[c]}, line ${l.line_no}`} help="" error={errors[`chg-l${l.line_no}-${c}`]}>
                      {c === "quantity" ? <Input autoComplete="off" inputMode="decimal" value={row[c]} onChange={e => setLine(l.line_no, c, e.target.value)} />
                        : <MoneyInput currency={po.currency} autoComplete="off" value={row[c]} onChange={e => setLine(l.line_no, c, e.target.value)} />}
                    </Field>}
                </td>)}
                <td data-numeric><Money zero="value" value={l.line_total} currency={po.currency} /></td>
              </tr>;
            })}</tbody>
          </table>
          <p className="req-sub">Project, department and ledger account cannot change in a change order. Use a budget transfer instead.</p>
        </section>
      </div>
      <div className="po-col">
        <DiffCard change={change} po={po} updating={updating} rejected={rejected} />
        <EffectCard change={change} name={project} />
        <section className="req-card" aria-label="Approval">
          <ApprovalLine change={change} />
          {editable && <button type="button" aria-busy={busy || undefined} onClick={() => void act(change.material ? "submit" : "apply")}>{change.material ? "Submit for Approval" : "Apply"}</button>}
        </section>
      </div>
    </div>
  </section>;
}
