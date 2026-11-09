import * as Dialog from "@radix-ui/react-dialog";
import { PaperPlaneTilt, X } from "@phosphor-icons/react";
import { useEffect, useRef, useState } from "react";
import { apiClient } from "../../../api/client";
import Money from "../../ui/Money";
import Person from "../../ui/Person";
import Signal from "../../ui/Signal";
import { failure, mutationHeaders, type Problem } from "../../requisitions/api";
import { useLoad } from "../../requisitions/useLoad";
import type { Po } from "../api";
import "../../../styles/vendors.css";
import "../../../styles/projects.css";

/** The value of TEMPLATE in modules/purchasing/transmission.py. */
const EMAIL_TEMPLATE = "po-issued-v1";
/** MASTER 7.16.4: what never goes to the vendor. A fixed list, never fetched. */
const STAYS_HERE = ["Chartfields (department and ledger account)", "Budget figures", "Internal notes"];

/** The confirmation is the commit (MASTER 7.16.1): it lists what goes out, what stays, and what is committed. */
export default function IssueDialog({ po, navigate, onReload }: { po: Po; navigate: (href: string) => void; onReload: () => void }) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false), [refusal, setRefusal] = useState<{ message: string; stale: boolean } | null>(null);
  const key = useRef<string | null>(null), inFlight = useRef(false);
  const opener = useRef<HTMLButtonElement>(null), safe = useRef<HTMLButtonElement>(null);
  const [opened, setOpened] = useState(0);
  const [lateHeld, setLateHeld] = useState(false);
  const heldNote = useRef<HTMLDivElement>(null);

  const copy = useLoad(async () => {
    if (!open) return null;
    const { data } = await apiClient.GET("/api/v1/purchase-orders/{id}/vendor-copy", { params: { path: { id: po.id } } });
    if (!data) throw new Error("The vendor copy could not be loaded.");
    return data;
  }, [po.id, open, opened]);
  const budget = useLoad(async () => {
    if (!open) return null;
    const { data, response } = await apiClient.GET("/api/v1/purchase-orders/{id}/budget-summary", { params: { path: { id: po.id } } });
    if (data) return { kind: "ok", projects: data.projects } as const;
    if (response?.status === 403) return { kind: "forbidden" } as const;
    throw new Error("Budget figures could not be loaded.");
  }, [po.id, open, opened]);

  const held = copy.data?.po_email_allowed === false || lateHeld;
  useEffect(() => { if (lateHeld) heldNote.current?.focus(); }, [lateHeld]);

  const issue = async () => {
    if (inFlight.current) return;
    inFlight.current = true; setBusy(true); setRefusal(null);
    try {
      const { response, error } = await apiClient.POST("/api/v1/purchase-orders/{id}/issue", { params: { path: { id: po.id } }, headers: mutationHeaders(key.current!, po.version), ...(held ? { body: { send_email: false } } : {}) });
      if (response.ok) { setOpen(false); navigate(`/purchase-orders/${po.id}/issue`); return; }
      const problem = error as Problem | undefined;
      if (problem?.checks?.problem === "po_email_held") { setLateHeld(true); key.current = crypto.randomUUID(); return; }
      setRefusal({ message: problem?.checks?.problem === "stale_version" ? "This order changed since you opened it." : failure(problem, "Issuing the purchase order"), stale: problem?.checks?.problem === "stale_version" });
    } catch { setRefusal({ message: failure(undefined, "Issuing the purchase order"), stale: false }); }
    finally { inFlight.current = false; setBusy(false); }
  };

  const vc = copy.data?.vendor_copy;
  return <Dialog.Root open={open} onOpenChange={next => { if (busy && !next) return; setOpen(next); }}>
    <button type="button" ref={opener} className="req-link-button" onClick={() => { key.current = crypto.randomUUID(); setRefusal(null); setLateHeld(false); setOpened(n => n + 1); setOpen(true); }}><PaperPlaneTilt aria-hidden="true" weight="regular" />Issue</button>
    <Dialog.Portal><Dialog.Overlay className="project-dialog-overlay" />
      <Dialog.Content className="project-dialog material-surface material-shadow req-dialog vendor-dialog" data-shadow="lg"
        onOpenAutoFocus={e => { e.preventDefault(); safe.current?.focus(); }}
        onCloseAutoFocus={e => { e.preventDefault(); opener.current?.focus(); }}
        onEscapeKeyDown={e => { if (busy) e.preventDefault(); }} onInteractOutside={e => { if (busy) e.preventDefault(); }}>
        <Dialog.Title>Issue {po.number}</Dialog.Title>
        <Dialog.Description>{held ? "Issuing commits the budget. No email goes to the vendor." : "Issuing sends this version to the vendor and commits the budget. Check what goes out."}</Dialog.Description>
        {held && <div ref={heldNote} tabIndex={-1}><Signal tone="warning" label="Purchase order email is held until the sending domain is verified" /><p>Issue without email, then download the PDF from the order and send it to the vendor yourself.</p></div>}
        <dl className="po-issue-facts">
          <dt>Recipients</dt><dd>{po.recipients.length ? <ul className="vendor-list-plain">{po.recipients.map((r, i) => <li key={i}><Person name={String(r.name)} email={String(r.email)} /></li>)}</ul>
            : "No recipient is set. Issuing sends to the vendor's document contacts and stops if it has none."}</dd>
          <dt>Version</dt><dd>v{po.revision + 1}</dd>
          <dt>Template</dt><dd>Email {EMAIL_TEMPLATE}, document {copy.data?.template_version ?? "…"}</dd>
        </dl>
        <h3>What will be sent</h3>
        {copy.error ? <p role="alert">The vendor copy could not be loaded. <button type="button" onClick={copy.reload}>Try again</button></p>
          : !vc ? <p className="req-skeleton" role="status" aria-label="Loading the vendor copy" />
          : <>
            <table className="po-lines"><caption className="visually-hidden">Lines on the vendor copy</caption><thead><tr><th scope="col">Line</th><th scope="col">Description</th><th scope="col" data-numeric>Quantity</th><th scope="col" data-numeric>Unit Price</th><th scope="col" data-numeric>Line Total</th></tr></thead>
              <tbody>{vc.lines.map(l => <tr key={l.line_no}><td>{l.line_no}</td><td>{l.description}</td><td data-numeric>{l.quantity} {l.uom_code}</td>
                <td data-numeric><Money value={l.unit_price} currency={vc.currency} /></td><td data-numeric><Money value={l.line_total} currency={vc.currency} /></td></tr>)}</tbody></table>
            <dl className="po-totals">{([["Price", vc.price_total], ["Tax", vc.tax_total], ["Freight", vc.freight_total], ["Total", vc.total]] as const).map(([t, v]) => <div key={t}><dt>{t}</dt><dd><Money value={v} currency={vc.currency} zero="value" /></dd></div>)}</dl>
            {(vc.notes ?? []).length > 0 && <><h4>Notes to the vendor</h4><ul className="vendor-list-plain">{(vc.notes ?? []).map((n, i) => <li key={i}>{n.body}</li>)}</ul></>}
          </>}
        <h3>What stays here</h3>
        <ul className="vendor-list-plain">{STAYS_HERE.map(s => <li key={s}>{s}</li>)}</ul>
        <h3>The commitment</h3>
        {budget.error ? <p role="alert">{budget.error} <button type="button" onClick={budget.reload}>Try again</button></p>
          : !budget.data ? <p className="req-skeleton" role="status" aria-label="Loading the commitment" />
          : budget.data.kind === "forbidden" ? <p>Budget figures are not visible to you.</p>
          : <ul className="vendor-list-plain">{budget.data.projects.map(p => <li key={p.project_code}>Issuing commits <Money value={p.this_po} currency={p.currency} zero="value" /> against {p.project_code} {p.project_name}.</li>)}</ul>}
        {refusal && <p role="alert" className="field-error">{refusal.message}{refusal.stale && <> <button type="button" onClick={() => { setOpen(false); onReload(); }}>Reload</button></>}</p>}
        <div className="req-actions">
          <button type="button" ref={safe} onClick={() => setOpen(false)} disabled={busy}>Not Yet</button>
          <button type="button" onClick={() => void issue()} aria-busy={busy || undefined} disabled={busy}>{busy ? "Issuing…" : held ? "Issue Without Email" : "Issue"}</button>
        </div>
        <Dialog.Close aria-label="Close dialog" className="grid-sheet-close" disabled={busy}><X aria-hidden="true" weight="regular" /></Dialog.Close>
      </Dialog.Content>
    </Dialog.Portal>
  </Dialog.Root>;
}
