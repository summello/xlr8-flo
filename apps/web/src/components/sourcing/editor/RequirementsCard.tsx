import { Plus, Trash } from "@phosphor-icons/react";
import { useState } from "react";
import { apiClient } from "../../../api/client";
import { failure } from "../../requisitions/api";
import { useLoad } from "../../requisitions/useLoad";
import type { CardProps } from "./cards";
import { write } from "./api";

/** Draft: choose a template, edit the list, waive with a reason (the lead only). Amend: read-only. */
export default function RequirementsCard({ rfq, mode, onRfq, report }: CardProps) {
  const [edited, setEdited] = useState<string[] | null>(null);
  const [adding, setAdding] = useState("");
  const [template, setTemplate] = useState("");
  const [waiving, setWaiving] = useState<{ id: string; reason: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const texts = edited ?? (rfq.requirements ?? []).map(r => r.text);
  const templates = useLoad(async () => {
    if (mode !== "edit") return null;
    const response = await apiClient.GET("/api/v1/master/{kind}", { params: { path: { kind: "rfq_requirement_template" }, query: { active: true } } });
    if (response.response.status === 403) return { denied: true as const, rows: [] };
    if (!response.data) throw new Error(failure(response.error, "Loading the requirement templates"));
    return { denied: false as const, rows: response.data.rows };
  }, [mode]);

  const run = async (act: (headers: Record<string, string>) => ReturnType<Parameters<typeof write>[0]>, action: string) => {
    setBusy(true); report(null);
    const done = await write(act, rfq, action);
    setBusy(false);
    if (done.rfq) { setEdited(null); setAdding(""); setTemplate(""); setWaiving(null); onRfq(done.rfq); } else report(done.message);
  };

  if (mode === "amend") return <section className="req-card" aria-labelledby="rfq-card-requirements">
    <header className="req-card-head"><h2 id="rfq-card-requirements">Compliance Requirements</h2></header>
    <p className="req-sub">Requirements cannot change after publication.</p>
    {(rfq.requirements ?? []).length === 0 ? <p>No requirements.</p> : <ul className="vendor-list-plain">{(rfq.requirements ?? []).map(r => <li key={r.id}>{r.text}{r.waived && <span className="req-sub">Waived: {r.waived_reason}</span>}</li>)}</ul>}
  </section>;

  return <section className="req-card" aria-labelledby="rfq-card-requirements" aria-busy={busy || undefined}>
    <header className="req-card-head"><h2 id="rfq-card-requirements">Compliance Requirements</h2></header>
    {templates.data && !templates.data.denied && templates.data.rows.length > 0 && <div className="rfq-filters">
      <div className="form-field"><label htmlFor="rfq-template">Requirements Template</label>
        <select id="rfq-template" value={template} onChange={e => setTemplate(e.target.value)}><option value="">Choose a template</option>{templates.data.rows.map(t => <option key={t.id} value={t.id}>{t.name}</option>)}</select></div>
      <button type="button" onClick={() => { if (template) void run(h => apiClient.POST("/api/v1/sourcing/rfqs/{id}/requirements/from-template", { params: { path: { id: rfq.id } }, headers: h, body: { template_id: template } }), "Adding the template"); else report("Choose a template to add."); }}>Add From Template</button>
    </div>}
    {templates.error && <div role="alert"><p>{templates.error}</p><button type="button" onClick={templates.reload}>Try again</button></div>}
    {texts.length === 0 ? <p>No requirements yet. Add one below or start from a template.</p> : <ul className="vendor-list-plain">{texts.map((text, n) => {
      const saved = (rfq.requirements ?? []).find(r => r.text === text && edited === null);
      return <li key={n} className="rfq-invitee">
        <div className="form-field"><label htmlFor={`rfq-requirement-${n}`}>Requirement {n + 1}</label>
          <input id={`rfq-requirement-${n}`} value={text} disabled={saved?.waived} onChange={e => setEdited(texts.map((t, i) => i === n ? e.target.value : t))} /></div>
        {saved?.waived && <span className="req-sub">Waived: {saved.waived_reason}</span>}
        <div className="req-actions">
          <button type="button" aria-label={`Remove requirement ${n + 1}`} onClick={() => setEdited(texts.filter((_, i) => i !== n))}><Trash aria-hidden="true" weight="regular" />Remove</button>
          {rfq.can_approve && saved && !saved.waived && <button type="button" aria-label={`Waive requirement ${n + 1}`} onClick={() => setWaiving({ id: saved.id, reason: "" })}>Waive</button>}
        </div>
        {waiving && saved && waiving.id === saved.id && <div className="form-field"><label htmlFor="rfq-waive-reason">Reason for waiving requirement {n + 1}</label>
          <input id="rfq-waive-reason" value={waiving.reason} onChange={e => setWaiving({ id: waiving.id, reason: e.target.value })} />
          <div className="req-actions"><button type="button" onClick={() => { if (waiving.reason.trim()) void run(h => apiClient.POST("/api/v1/sourcing/rfqs/{id}/requirements/{rid}/waive", { params: { path: { id: rfq.id, rid: saved.id } }, headers: h, body: { reason: waiving.reason.trim() } }), "Waiving the requirement"); else report("Enter a reason to waive the requirement."); }}>Confirm Waiver</button>
            <button type="button" onClick={() => setWaiving(null)}>Keep Requirement</button></div></div>}
      </li>;
    })}</ul>}
    <div className="rfq-filters">
      <div className="form-field"><label htmlFor="rfq-new-requirement">New Requirement</label>
        <input id="rfq-new-requirement" value={adding} onChange={e => setAdding(e.target.value)} /></div>
      <button type="button" onClick={() => { if (adding.trim()) { setEdited([...texts, adding.trim()]); setAdding(""); } else report("Enter the requirement to add."); }}><Plus aria-hidden="true" weight="regular" />Add Requirement</button>
    </div>
    {edited !== null && <div className="req-actions">
      <button type="button" aria-busy={busy || undefined} onClick={() => { const list = texts.map(t => t.trim()).filter(Boolean); void run(h => apiClient.PUT("/api/v1/sourcing/rfqs/{id}/requirements", { params: { path: { id: rfq.id } }, headers: h, body: { requirements: list } }), "Saving the requirements"); }}>Save Requirements</button>
      <button type="button" onClick={() => setEdited(null)}>Discard Requirement Changes</button>
    </div>}
  </section>;
}
