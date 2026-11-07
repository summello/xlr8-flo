import { FloppyDisk, PaperPlaneTilt } from "@phosphor-icons/react";
import { useEffect, useRef, useState } from "react";
import { apiClient } from "../../../api/client";
import type { components } from "../../../api/generated/schema";
import ErrorSummary, { type SummaryError } from "../../form/ErrorSummary";
import Person from "../../ui/Person";
import Field from "../../form/Field";
import { failure, mutationHeaders, type Problem } from "../../requisitions/api";
import { useLoad } from "../../requisitions/useLoad";
import type { Rfq } from "../api";
import { CARDS } from "./cards";
import PublishDialog from "./PublishDialog";
import Textarea from "./Textarea";
import { build, changes, fetchRfq, formOf, summaryOf, type Mode } from "./api";

type Body = components["schemas"]["RfqContentPatch"];
type Submission = { fingerprint: string; key: string };

const back = (navigate: (href: string) => void, id: string) => <a href={`/sourcing/rfqs/${id}`} onClick={e => { e.preventDefault(); navigate(`/sourcing/rfqs/${id}`); }}>Back to the RFQ</a>;

/** `/sourcing/rfqs/:id/edit` (draft) and `/amend` (published): the same cards, a different set of things that can change. */
export default function Editor({ id, mode, navigate }: { id: string; mode: Mode; navigate: (href: string) => void }) {
  const load = useLoad(() => fetchRfq(id), [id]);
  if (load.error) return <section className="req-page" aria-label="RFQ editor"><div role="alert"><p>{load.error}</p><button type="button" onClick={load.reload}>Try again</button></div>{back(navigate, id)}</section>;
  if (!load.data) return <section className="req-page" aria-label="RFQ editor"><div className="req-card req-card-skeleton" aria-busy="true"><p className="req-skeleton" role="status" aria-label="Loading RFQ" /></div></section>;
  const rfq = load.data;
  const wrong = mode === "edit" ? (rfq.status !== "draft" ? "Only a draft RFQ can be edited. A published RFQ changes through an amendment." : null) : (rfq.status !== "published" ? "Only a published RFQ can be amended. Edit a draft instead." : null);
  if (wrong || !rfq.can_edit) return <section className="req-page" aria-label="RFQ editor"><p role="alert">{wrong ?? "You cannot change this RFQ. Ask for the RFQ edit permission on its business unit."}</p>{back(navigate, id)}</section>;
  return <Body key={rfq.id} rfq={rfq} mode={mode} onRfq={load.set} navigate={navigate} />;
}

function Body({ rfq, mode, onRfq, navigate }: { rfq: Rfq; mode: Mode; onRfq: (next: Rfq) => void; navigate: (href: string) => void }) {
  const [form, setForm] = useState(() => formOf(rfq));
  const [problems, setProblems] = useState<SummaryError[]>([]);
  const [linesNote, setLinesNote] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState<string | null>(null);
  const [confirm, setConfirm] = useState(false);
  const [publishMessage, setPublishMessage] = useState<string | null>(null);
  const submission = useRef<Submission | null>(null);
  const inFlight = useRef(false);
  const summary = useRef<HTMLDivElement>(null);
  const errors = Object.fromEntries(problems.map(p => [p.fieldId, p.message]));
  const shown = changes(rfq, form);

  useEffect(() => {
    if (!problems.length) return;
    const target = summary.current?.querySelector<HTMLElement>("[role=alert]");
    if (target) { target.tabIndex = -1; target.focus(); }
  }, [problems]);

  const report = (message: string | null) => { setSaved(null); setProblems(message ? [{ fieldId: "rfq-form", label: "RFQ", message }] : []); };
  const refuse = (problem: Problem | undefined, action: string) => {
    if (problem?.checks?.problem === "requisition_sourced") setLinesNote(problem.detail ?? "That requisition is fully sourced, so a quantity cannot be reduced.");
    setProblems(problem?.checks?.problem === "requisition_sourced" ? [{ fieldId: "rfq-lines", label: "Lines", message: problem.detail ?? "That requisition is fully sourced." }] : summaryOf(problem, action));
  };

  /** Draft: send what is on screen; false means nothing was saved and the summary says why. */
  const save = async (): Promise<boolean> => {
    setSaved(null); setLinesNote(null);
    const built = build(rfq, form, "edit");
    if (built.errors.length) { setProblems(built.errors); return false; }
    if (shown.length === 0) { setProblems([]); return true; }
    setBusy(true);
    try {
      const { data, error } = await apiClient.PATCH("/api/v1/sourcing/rfqs/{id}", { params: { path: { id: rfq.id } }, headers: mutationHeaders(crypto.randomUUID(), rfq.edit_revision), body: built.body as Body });
      if (!data) { refuse(error as Problem | undefined, "Saving the draft"); return false; }
      onRfq(data); setForm(formOf(data)); setProblems([]); setSaved("Draft saved.");
      return true;
    } catch { report(failure(undefined, "Saving the draft")); return false; } finally { setBusy(false); }
  };

  const publish = async () => {
    setBusy(true); setPublishMessage(null);
    try {
      const { data, error } = await apiClient.POST("/api/v1/sourcing/rfqs/{id}/publish", { params: { path: { id: rfq.id } }, headers: mutationHeaders(submission.current?.key ?? crypto.randomUUID()) });
      if (data) { navigate(`/sourcing/rfqs/${rfq.id}`); return; }
      const problem = error as Problem | undefined;
      if (problem?.errors?.length) { setConfirm(false); setProblems(summaryOf(problem, "Publishing the RFQ")); } else setPublishMessage(failure(problem, "Publishing the RFQ"));
    } catch { setPublishMessage(failure(undefined, "Publishing the RFQ")); } finally { setBusy(false); }
  };

  const amend = async () => {
    if (inFlight.current) return;
    setLinesNote(null);
    const built = build(rfq, form, "amend");
    const found = [...built.errors];
    if (reason.trim() === "") found.push({ fieldId: "amend-reason", label: "Reason", message: "Enter a reason for the amendment." });
    else if (reason.length > 500) found.push({ fieldId: "amend-reason", label: "Reason", message: "Enter at most 500 characters." });
    if (found.length === 0 && shown.length === 0) found.push({ fieldId: "amend-reason", label: "What Changed", message: "Change something to amend the RFQ." });
    if (found.length) { setProblems(found); return; }
    const body = { reason: reason.trim(), ...built.body };
    const fingerprint = JSON.stringify(body);
    if (submission.current?.fingerprint !== fingerprint) submission.current = { fingerprint, key: crypto.randomUUID() };
    inFlight.current = true; setBusy(true); setProblems([]);
    try {
      const { data, error } = await apiClient.POST("/api/v1/sourcing/rfqs/{id}/amend", { params: { path: { id: rfq.id } }, headers: mutationHeaders(submission.current.key), body: body as components["schemas"]["RfqAmend"] });
      if (data) { submission.current = null; navigate(`/sourcing/rfqs/${rfq.id}`); return; }
      refuse(error as Problem | undefined, "Amending the RFQ");
    } catch { report(failure(undefined, "Amending the RFQ")); } finally { inFlight.current = false; setBusy(false); }
  };

  const told = (rfq.invitees ?? []).map(i => ({ name: String(i.vendor_snapshot.trading_name ?? "Vendor"), to: (i.contacts ?? []).filter(c => c.chosen) }));

  return <section className="req-page" aria-label={mode === "edit" ? "Edit RFQ" : "Amend RFQ"}>
    <header className="req-heading"><p>{mode === "edit" ? `${rfq.number} · ${rfq.title}. Draft: nothing reaches a vendor until you publish.` : `${rfq.number} · ${rfq.title}. Version ${rfq.version}: an amendment makes version ${rfq.version + 1} and tells every invitee.`}</p></header>
    <div ref={summary}><ErrorSummary errors={problems} /></div>
    {saved && <p role="status">{saved}</p>}
    <div id="rfq-form" tabIndex={-1} className="req-grid-detail">
      {CARDS.map(card => <div key={card.id} className="req-wide">{card.render({ rfq, mode, form, setForm: change => setForm(change), errors, linesNote, onRfq, report })}</div>)}
      {mode === "amend" && <>
        <section className="req-card req-wide" aria-labelledby="rfq-card-changed">
          <header className="req-card-head"><h2 id="rfq-card-changed">What Changed</h2></header>
          {shown.length === 0 ? <p>Nothing has changed yet.</p> : <ul className="vendor-list-plain">{shown.map(c => <li key={c.key}>{c.label}: now {c.now}, <span className="visually-hidden">was </span><s className="rfq-was">{c.was}</s></li>)}</ul>}
        </section>
        <section className="req-card req-wide" aria-labelledby="rfq-card-reason">
          <header className="req-card-head"><h2 id="rfq-card-reason">Reason</h2></header>
          <Field id="amend-reason" label="Reason for the Amendment" required help="Every invitee is told why." error={errors["amend-reason"]}>
            <Textarea value={reason} onChange={e => setReason(e.target.value)} />
          </Field>
        </section>
        <section className="req-card req-wide" aria-labelledby="rfq-card-told">
          <header className="req-card-head"><h2 id="rfq-card-told">Who Will Be Told</h2></header>
          {told.length === 0 ? <p>No vendors are invited.</p> : <ul className="vendor-list-plain">{told.map(t => <li key={t.name}><strong>{t.name}</strong><span className="req-sub">{t.to.length === 0 ? "No contact chosen" : t.to.map((c, n) => <span key={c.id}>{n > 0 && ", "}<Person name={c.name} email={c.email} /></span>)}</span></li>)}</ul>}
        </section>
      </>}
    </div>
    <div className="req-actions">
      {mode === "edit" ? <>
        <button type="button" aria-busy={busy || undefined} onClick={() => void save()}><FloppyDisk aria-hidden="true" weight="regular" />Save Draft</button>
        <button type="button" className="form-submit" aria-busy={busy || undefined} onClick={() => void save().then(ok => { if (ok) setConfirm(true); })}><PaperPlaneTilt aria-hidden="true" weight="regular" />Publish RFQ</button>
      </> : <button type="button" className="form-submit" aria-busy={busy || undefined} onClick={() => void amend()}><PaperPlaneTilt aria-hidden="true" weight="regular" />{busy ? "Amending" : "Amend RFQ"}</button>}
    </div>
    {mode === "edit" && <PublishDialog rfq={rfq} open={confirm} busy={busy} message={publishMessage} onOpenChange={next => { setConfirm(next); if (!next) setPublishMessage(null); }} onConfirm={() => void publish()} />}
  </section>;
}
