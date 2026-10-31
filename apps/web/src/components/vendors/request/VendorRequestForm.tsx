import { CheckCircle, FloppyDisk, PaperPlaneTilt, UploadSimple } from "@phosphor-icons/react";
import { useEffect, useRef, useState } from "react";
import { apiClient } from "../../../api/client";
import type { components } from "../../../api/generated/schema";
import { writeHeaders } from "../../../features/auth/api";
import { registerGuard } from "../../../lib/navigation-guard";
import SubmitSummary from "../../requisitions/SubmitSummary";
import ReqField from "../../requisitions/ReqField";
import { failure, mutationHeaders, type Problem as ApiProblem } from "../../requisitions/api";
import { useLoad } from "../../requisitions/useLoad";
import Signal from "../../ui/Signal";
import { patchHeaders } from "../questionnaire/api";
import {
  CONTACT_LABELS, CONTACT_TYPES, EMPTY, clientProblems, complete, duplicateQuery, hasAnswer, matchText, problemFor,
  type Answer, type ContactType, type Form, type Problem, type Question,
} from "./model";
import "../../../styles/projects.css";
import "../../../styles/requisitions.css";
import "../../../styles/vendors.css";

type Match = components["schemas"]["DuplicateMatch"];
type Saved = { id: string; number: string; version: number; sensitive: number; onboarding: number; contacts: Partial<Record<ContactType, { id: string; version: number }>> };
type Doc = components["schemas"]["DocState"];
type Summary = { title: string; lead: string; problems: Problem[]; detail?: string };

const NOT_SET_UP = "Vendors cannot be submitted yet: no approval workflow is published. Your draft is saved. Ask an administrator to set one up.";
const asProblems = (e: ApiProblem | undefined) => (e?.errors ?? []).map(x => problemFor(x.field, x.message));

const vendorFields = (f: Form) => ({ trading_name: f.trading_name.trim(), legal_name: f.legal_name.trim(), availability: f.availability, unit_ids: f.availability === "restricted" ? f.unit_ids : [], identifiers: Object.fromEntries(Object.entries(f.identifiers).filter(([, v]) => v.trim())) });
const vendorBody = (f: Form) => JSON.stringify(vendorFields(f));
const answerBody = (f: Form) => Object.fromEntries(Object.entries(f.answers).filter(([, v]) => hasAnswer(v)));

/** Request a vendor (VEN-004): one form for the new request and for the requester's own draft. */
export default function VendorRequestForm({ navigate, id }: { navigate: (href: string) => void; id?: string }) {
  const [form, setForm] = useState<Form>(EMPTY);
  const [questions, setQuestions] = useState<Question[]>([]);
  const [saved, setSaved] = useState<Saved | null>(null);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<"save" | "submit" | null>(null);
  const [status, setStatus] = useState("Not saved yet");
  const [notice, setNotice] = useState<string | null>(null);
  const [dirty, setDirty] = useState(false);
  const [matches, setMatches] = useState<Match[]>([]);
  const [override, setOverride] = useState(false);
  const [done, setDone] = useState<{ number: string; id: string } | null>(null);
  const [canOverride, setCanOverride] = useState(false);
  const [taxSaved, setTaxSaved] = useState(false);
  const key = useRef<string | null>(null);
  const lastTax = useRef("");
  const snap = useRef({ vendor: "", answers: "", contacts: {} as Record<string, string> });
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined);
  const check = useRef(0);

  const units = useLoad(async () => {
    const { data, error } = await apiClient.GET("/api/v1/org/units", { params: { query: { page_size: 100 } } });
    if (!data) throw new Error(failure(error, "Loading the units"));
    return data.rows;
  }, []);
  const idTypes = useLoad(async () => (await apiClient.GET("/api/v1/master/{kind}", { params: { path: { kind: "vendor_identifier_type" }, query: { page_size: 50 } } })).data?.rows ?? [], []);
  // The required document types: the real list for the saved request, with its own error and retry.
  const docList = useLoad<Doc[]>(async () => {
    if (!saved) return [];
    const { data, error } = await apiClient.GET("/api/v1/vendors/{id}/documents", { params: { path: { id: saved.id } } });
    if (!data) throw new Error(failure(error, "Loading the required documents"));
    return data;
  }, [saved?.id]);

  useEffect(() => registerGuard(() => dirty && done === null), [dirty, done]);

  const set = (patch: Partial<Form>) => { key.current = null; setDirty(true); setForm(f => ({ ...f, ...patch })); };
  const setContact = (t: ContactType, patch: Partial<Form["contacts"][ContactType]>) => set({ contacts: { ...form.contacts, [t]: { ...form.contacts[t], ...patch } } });
  const setAnswer = (k: string, v: Answer) => set({ answers: { ...form.answers, [k]: v } });
  const errorFor = (target: string) => errors[target];
  const show = (s: Summary) => { setSummary(s); setErrors(Object.fromEntries(s.problems.map(p => [p.target, p.message]))); };

  // Load the requester's own draft. Tax and bank values are write-only: no response ever refills them (PRIV-001).
  const load = useLoad(async () => {
    if (!id) return null;
    const [vendor, contacts, onboarding] = await Promise.all([
      apiClient.GET("/api/v1/vendors/{id}", { params: { path: { id } } }),
      apiClient.GET("/api/v1/vendors/{id}/contacts", { params: { path: { id } } }),
      apiClient.GET("/api/v1/vendors/{id}/onboarding", { params: { path: { id } } }),
    ]);
    if (!vendor.data) throw new Error(failure(vendor.error, "Loading the draft"));
    const v = vendor.data, next: Form = {
      ...EMPTY, trading_name: v.trading_name, legal_name: v.legal_name, availability: v.availability,
      unit_ids: (v.units ?? []).map(u => u.unit_id), identifiers: { ...(v.identifiers ?? {}) },
      contacts: { billing: { ...EMPTY.contacts.billing }, service_delivery: { ...EMPTY.contacts.service_delivery } },
      answers: (onboarding.data?.answers ?? {}) as Form["answers"],
    };
    const rows: Saved["contacts"] = {};
    for (const c of contacts.data ?? []) if (c.type === "billing" || c.type === "service_delivery") {
      next.contacts[c.type] = { name: c.name, email: c.email, phone: c.phone ?? "", receives: c.receives_documents };
      rows[c.type] = { id: c.id, version: c.version };
    }
    setForm(next); setQuestions(onboarding.data?.questions ?? []); 
    setTaxSaved(Boolean(v.sensitive?.tax_id));
    setSaved({ id: v.id, number: v.number, version: v.version, sensitive: v.sensitive?.version ?? 0, onboarding: onboarding.data?.row_version ?? 0, contacts: rows });
    snap.current = { vendor: vendorBody(next), answers: JSON.stringify(answerBody(next)), contacts: Object.fromEntries(CONTACT_TYPES.map(t => [t, JSON.stringify(next.contacts[t])])) };
    setStatus(v.status === "draft" ? `Draft ${v.number} loaded` : `${v.number} was submitted`);
    if (v.status !== "draft") setNotice(`${v.number} was submitted for approval and can no longer be edited here.`);
    return v;
  }, [id]);

  /** One step of a save: returns false after showing why. Everything typed stays on screen (UX-005). */
  const fail = (error: ApiProblem | undefined, action: string): false => {
    const problems = asProblems(error);
    const stale = error?.checks?.problem === "stale_version";
    show({ title: problems.length ? `Fix ${problems.length} ${problems.length === 1 ? "thing" : "things"} and try again` : `${action} did not finish`, lead: "Everything you entered is kept.", problems, detail: problems.length ? undefined : failure(error ?? undefined, action) + (stale ? " Reload the page to pick up the latest version, then try again." : "") });
    return false;
  };

  const persist = async (): Promise<Saved | null> => {
    let s = saved;
    const body = vendorFields(form);
    if (!s) {
      key.current ??= crypto.randomUUID();
      const r = await apiClient.POST("/api/v1/vendors/onboarding-requests", { headers: mutationHeaders(`${key.current}-create`), body });
      if (!r.data) { fail(r.error as ApiProblem, "Saving the draft"); return null; }
      // ponytail: a new vendor starts at version 1 and only this form moves it; a reload re-reads it from GET /vendors/{id}.
      s = { id: r.data.vendor_id, number: r.data.vendor_number, version: 1, sensitive: 0, onboarding: r.data.row_version, contacts: {} };
      snap.current.vendor = vendorBody(form);
      setQuestions(r.data.questions);
      window.history.replaceState(null, "", `/vendors/${s.id}/edit`); // a reload now reopens this draft
    } else if (snap.current.vendor !== vendorBody(form)) {
      const r = await apiClient.PATCH("/api/v1/vendors/{id}", { params: { path: { id: s.id } }, headers: patchHeaders(s.version), body });
      if (!r.data) { fail(r.error as ApiProblem, "Saving the company details"); return null; }
      s = { ...s, version: r.data.version }; snap.current.vendor = vendorBody(form);
    }
    const draftId = s.id;
    for (const t of CONTACT_TYPES) {
      const c = form.contacts[t], now = JSON.stringify(c);
      if (!c.name.trim() || !c.email.trim() || snap.current.contacts[t] === now) continue;
      const fields = { name: c.name.trim(), email: c.email.trim(), receives_documents: c.receives, ...(c.phone.trim() ? { phone: c.phone.trim() } : {}) };
      const row: { id: string; version: number } | undefined = s.contacts[t];
      const r: { data?: { id: string; version: number }; error?: unknown } = row
        ? await apiClient.PATCH("/api/v1/vendors/{id}/contacts/{contact_id}", { params: { path: { id: draftId, contact_id: row.id } }, headers: patchHeaders(row.version), body: fields })
        : await apiClient.POST("/api/v1/vendors/{id}/contacts", { params: { path: { id: draftId } }, headers: mutationHeaders(crypto.randomUUID()), body: { type: t, ...fields } });
      if (!r.data) { fail(r.error as ApiProblem, `Saving the ${CONTACT_LABELS[t]} contact`); return null; }
      s = { ...s, contacts: { ...s.contacts, [t]: { id: r.data.id, version: r.data.version } } }; snap.current.contacts[t] = now;
    }
    const tax = form.tax_id.trim();
    const sent = `${tax}|${form.tax_id_country}`; // the country is part of what was saved
    if (tax && sent !== lastTax.current) {
      const r = await apiClient.PUT("/api/v1/vendors/{id}/sensitive", { params: { path: { id: draftId } }, headers: patchHeaders(s.sensitive), body: { tax_id: tax, tax_id_country: form.tax_id_country } });
      if (!r.data) { fail(r.error as ApiProblem, "Saving the tax id"); return null; }
      s = { ...s, sensitive: r.data.version }; lastTax.current = sent; setTaxSaved(true);
    }
    const answers = answerBody(form);
    if (questions.length > 0 || s.onboarding > 0) if (JSON.stringify(answers) !== snap.current.answers) {
      const r = await apiClient.PUT("/api/v1/vendors/{id}/onboarding/answers", { params: { path: { id: draftId } }, headers: patchHeaders(s.onboarding), body: { answers: answers as never } });
      if (!r.data) { fail(r.error as ApiProblem, "Saving the answers"); return null; }
      s = { ...s, onboarding: r.data.row_version }; snap.current.answers = JSON.stringify(answers);
    }
    setSaved(s); setStatus(`Draft ${s.number} saved`); setDirty(false);
    return s;
  };

  const saveDraft = async () => {
    if (busy) return;
    setBusy("save"); setSummary(null); setErrors({}); setNotice(null);
    const local = clientProblems(form, questions, "save");
    if (local.length) show({ title: `Fix ${local.length} ${local.length === 1 ? "thing" : "things"} before you save`, lead: "Nothing was saved. Everything you entered is kept.", problems: local });
    else if (await persist()) setNotice("Draft saved.");
    setBusy(null);
  };

  const submit = async () => {
    if (busy) return;
    setBusy("submit"); setSummary(null); setErrors({}); setNotice(null);
    const local = clientProblems(form, questions, "submit");
    if (local.length) { show({ title: `Fix ${local.length} ${local.length === 1 ? "thing" : "things"} before you submit`, lead: "Nothing was submitted. Everything you entered is kept.", problems: local }); setBusy(null); return; }
    const s = await persist();
    if (!s) { setBusy(null); return; }
    key.current ??= crypto.randomUUID();
    const reason = override ? form.override_reason.trim() : "";
    const r = await apiClient.POST("/api/v1/vendors/{id}/submit", { params: { path: { id: s.id } }, headers: mutationHeaders(`${key.current}-submit`), body: reason ? { override_reason: reason } : {} });
    if (r.data) { key.current = null; setDone({ number: s.number, id: s.id }); setDirty(false); setBusy(null); return; }
    const e = r.error as ApiProblem | undefined, p = e?.checks?.problem;
    if (p === "workflow_not_set_up") show({ title: "Vendors cannot be submitted yet", lead: "Your draft is saved. Everything you entered is kept.", problems: [], detail: NOT_SET_UP });
    else if (p === "duplicate_identifier") {
      const problems = asProblems(e);
      show({ title: "This vendor may already exist", lead: "Your draft is saved. Everything you entered is kept.", problems, detail: canOverride ? "Give a reason to submit it anyway." : "Use the existing vendor, or ask a vendor manager to submit it with a reason." });
      void runCheck(form); // names the match at its field and says whether this caller may override
    } else if (p === "contacts_incomplete" || p === "answers_incomplete" || asProblems(e).length) fail(e, "Submitting");
    else show({ title: "Submitting did not finish", lead: "Your draft is saved. Everything you entered is kept.", problems: [], detail: failure(e ?? undefined, "Submitting") });
    setBusy(null);
  };

  const runCheck = async (form: Form) => {
    const query = duplicateQuery(form), n = ++check.current;
    if (Object.keys(query).length === 0) { setMatches([]); setCanOverride(false); return; }
    // identifiers[type] keys are not in the generated query type; the server reads them from the raw query string.
    const res = await apiClient.GET("/api/v1/vendors/duplicate-check", { params: { query: query as never } }).catch(() => undefined);
    if (n === check.current && res?.data) { setMatches(res.data.matches); setCanOverride(res.data.can_override ?? false); }
  };
  // Debounced, once per blur: the server never sees a request per keystroke.
  const duplicateCheck = () => { clearTimeout(timer.current); timer.current = setTimeout(() => void runCheck(form), 400); };
  useEffect(() => () => clearTimeout(timer.current), []);

  const upload = async (file: File, then: (attachmentId: string) => Promise<void>) => {
    const s = saved ?? await persist();
    if (!s) return;
    const data = new FormData(); data.set("file", file);
    const res = await fetch(`/api/v1/documents/vendor/${s.id}/attachments`, { method: "POST", credentials: "same-origin", headers: writeHeaders(), body: data }).catch(() => undefined);
    if (!res?.ok) { const e = await res?.json().catch(() => undefined) as ApiProblem | undefined; show({ title: "The file was not uploaded", lead: "Everything you entered is kept.", problems: [], detail: failure(e, "Uploading the file") }); return; }
    await then(((await res.json()) as { id: string }).id);
  };
  const addDocument = (type_code: string, file: File) => upload(file, async attachment_id => {
    const r = await apiClient.POST("/api/v1/vendors/{id}/documents", { params: { path: { id: saved!.id } }, headers: mutationHeaders(crypto.randomUUID()), body: { type_code, attachment_id } });
    if (r.data) docList.reload();
    else show({ title: "The document was not attached", lead: "Everything you entered is kept.", problems: [], detail: failure(r.error as ApiProblem, "Attaching the document") });
  });

  const dupFor = (field: string) => matches.find(m => m.field === field);
  const dup = (field: string) => {
    const m = dupFor(field);
    if (!m) return null;
    return <div className="vr-dup">
      <Signal tone="warning" role="status" label={matchText(m)} />
      {m.vendor_id && <a href={`/vendors/${m.vendor_id}`} onClick={e => { e.preventDefault(); navigate(`/vendors/${m.vendor_id}`); }}>Use Existing Vendor</a>}
      {canOverride && <button type="button" className="req-link-button" aria-expanded={override} onClick={() => setOverride(o => !o)}>Override with a Reason</button>}
    </div>;
  };

  if (done) return <section className="req-page req-confirmation" aria-labelledby="vr-done-title">
    <div className="req-card">
      <h2 id="vr-done-title" tabIndex={-1} ref={el => el?.focus()}>{done.number} Submitted for Approval</h2>
      <p role="status">The vendor approvers named in the approval workflow will review it. You will be told when it is decided, and the vendor becomes available once it is approved.</p>
      <div className="req-actions">
        <a className="form-submit" href={`/vendors/${done.id}`} onClick={e => { e.preventDefault(); navigate(`/vendors/${done.id}`); }}>View vendor</a>
        <a className="req-link-button" href="/vendors" onClick={e => { e.preventDefault(); navigate("/vendors"); }}>Back to vendors</a>
      </div>
    </div>
  </section>;
  if (id && load.loading && !load.data) return <section className="req-page" aria-label="Request a vendor"><div className="req-card req-card-skeleton" aria-busy="true"><p className="req-skeleton" role="status" aria-label="Loading the draft" /></div></section>;
  if (id && load.error && !load.data) return <section className="req-page" aria-label="Request a vendor"><div role="alert" className="req-notice"><p>{load.error}</p><button type="button" onClick={load.reload}>Try again</button></div></section>;
  const locked = Boolean(id && load.data && load.data.status !== "draft");
  const choices = (q: Question) => q.choices ?? [];

  return <section className="req-page vendor-request" aria-label="Request a vendor">
    <header className="req-heading">
      <p>{saved ? `${saved.number} · ${status}` : "Draft · not saved yet"}</p>
      <a href="/vendors" onClick={e => { e.preventDefault(); navigate("/vendors"); }}>Back to vendors</a>
    </header>
    {summary && <SubmitSummary title={summary.title} lead={summary.lead} problems={summary.problems} detail={summary.detail} />}
    {notice && <p className="req-note" role="status">{notice}</p>}
    <fieldset className="vr-fields" disabled={locked}>
      <div className="req-grid"><div className="req-main">
        <section className="req-card" aria-labelledby="vr-company"><header className="req-card-head"><h2 id="vr-company">Company</h2></header>
          <div className="req-fields">
            <ReqField id="vr-trading_name" label="Trading name" help="The name people know the vendor by." error={errorFor("vr-trading_name")}>
              {p => <input {...p} type="text" maxLength={200} value={form.trading_name} onChange={e => set({ trading_name: e.target.value })} />}</ReqField>
            <ReqField id="vr-legal_name" label="Legal name" help="As it appears on the vendor's registration and invoices." error={errorFor("vr-legal_name")}>
              {p => <input {...p} type="text" maxLength={200} value={form.legal_name} onChange={e => set({ legal_name: e.target.value })} />}</ReqField>
          </div></section>
        <section className="req-card" aria-labelledby="vr-identifiers"><header className="req-card-head"><h2 id="vr-identifiers">Identifiers</h2></header>
          <div className="req-fields">
            <ReqField id="vr-tax_id" label="Tax id" required={false} help={taxSaved ? "A tax id is saved and is not shown again. Type a new one to replace it." : "Checked for duplicates when you leave the field. It is never shown again after saving."} error={errorFor("vr-tax_id")}>
              {p => <input {...p} type="text" autoComplete="off" maxLength={64} value={form.tax_id} onChange={e => set({ tax_id: e.target.value })} onBlur={duplicateCheck} />}</ReqField>
            <ReqField id="vr-tax_id_country" label="Tax id country" required={false} help="The two-letter country code that issued it, such as US." error={errorFor("vr-tax_id_country")}>
              {p => <input {...p} type="text" autoComplete="off" maxLength={2} value={form.tax_id_country} onChange={e => set({ tax_id_country: e.target.value.toUpperCase() })} onBlur={duplicateCheck} />}</ReqField>
            {dup("tax_id")}
            {(idTypes.data ?? []).map(t => <div key={t.code} className="vr-id">
              <ReqField id={`vr-id-${t.code}`} label={t.name} required={false} help={`Checked for duplicates when you leave the field.`} error={errorFor(`vr-id-${t.code}`)}>
                {p => <input {...p} type="text" autoComplete="off" maxLength={64} value={form.identifiers[t.code] ?? ""} onChange={e => set({ identifiers: { ...form.identifiers, [t.code]: e.target.value } })} onBlur={duplicateCheck} />}</ReqField>
              {dup(`identifiers.${t.code}`)}</div>)}
          </div>
          {override && <ReqField id="vr-override" label="Reason for overriding" wide help="Recorded in the audit log with your submission." error={errorFor("vr-override")}>
            {p => <textarea {...p} rows={2} maxLength={500} value={form.override_reason} onChange={e => set({ override_reason: e.target.value })} />}</ReqField>}
        </section>
        <section className="req-card" aria-labelledby="vr-scope"><header className="req-card-head"><h2 id="vr-scope">Where It Applies</h2></header>
          <fieldset className="req-mode" id="vr-availability" tabIndex={-1}>
            <legend>Available to</legend>
            <label><input type="radio" name="availability" checked={form.availability === "global"} onChange={() => set({ availability: "global" })} />Every unit</label>
            <label><input type="radio" name="availability" checked={form.availability === "restricted"} onChange={() => set({ availability: "restricted" })} />Selected units</label>
          </fieldset>
          {form.availability === "restricted" && <ReqField id="vr-unit_ids" label="Units" help="Hold Ctrl or Command to choose several." error={errorFor("vr-unit_ids") ?? units.error ?? undefined}>
            {p => <><select {...p} multiple size={5} value={form.unit_ids} onChange={e => set({ unit_ids: Array.from(e.target.selectedOptions, o => o.value) })}>
              {(units.data ?? []).map(u => <option key={u.id} value={u.id}>{u.name}</option>)}</select>
              {units.error && <button type="button" onClick={units.reload}>Try again</button>}</>}</ReqField>}
        </section>
        <section className="req-card" aria-labelledby="vr-contacts"><header className="req-card-head"><h2 id="vr-contacts">Contacts</h2></header>
          {CONTACT_TYPES.map(t => <fieldset key={t} className="vr-contact"><legend>{CONTACT_LABELS[t]}</legend>
            <div className="req-fields">
              <ReqField id={`vr-${t}-name`} label="Name" help="Who we write to." error={errorFor(`vr-${t}-name`)}>
                {p => <input {...p} type="text" maxLength={200} value={form.contacts[t].name} onChange={e => setContact(t, { name: e.target.value })} />}</ReqField>
              <ReqField id={`vr-${t}-email`} label="Email" help="Where messages are sent." error={errorFor(`vr-${t}-email`)}>
                {p => <input {...p} type="email" maxLength={254} value={form.contacts[t].email} onChange={e => setContact(t, { email: e.target.value })} />}</ReqField>
              <ReqField id={`vr-${t}-phone`} label="Phone" required={false} help="Optional." error={errorFor(`vr-${t}-phone`)}>
                {p => <input {...p} type="tel" maxLength={40} value={form.contacts[t].phone} onChange={e => setContact(t, { phone: e.target.value })} />}</ReqField>
            </div>
            <label className="vr-check"><input type="checkbox" checked={form.contacts[t].receives} onChange={e => setContact(t, { receives: e.target.checked })} />RFQs and POs</label>
          </fieldset>)}
        </section>
        <section className="req-card" aria-labelledby="vr-questionnaire"><header className="req-card-head"><h2 id="vr-questionnaire">Questionnaire</h2></header>
          {questions.length === 0 ? <p>{saved ? "There are no questions to answer." : "Save the draft to load the questions."}</p> :
            <div className="req-fields">{questions.map(q => {
              const a = form.answers[q.key], fid = `vr-q-${q.key}`;
              return <ReqField key={q.key} id={fid} label={q.prompt} required={q.required} wide={q.type === "text" || q.type === "multi_choice"} help={q.required ? "Required." : "Optional."} error={errorFor(fid)}>
                {p => q.type === "text" ? <textarea {...p} rows={2} maxLength={2000} value={(a as string) ?? ""} onChange={e => setAnswer(q.key, e.target.value)} /> :
                  q.type === "number" ? <input {...p} type="text" inputMode="decimal" value={(a as string) ?? ""} onChange={e => setAnswer(q.key, e.target.value)} /> :
                  q.type === "date" ? <input {...p} type="date" value={(a as string) ?? ""} onChange={e => setAnswer(q.key, e.target.value)} /> :
                  q.type === "yes_no" ? <select {...p} value={a === true ? "yes" : a === false ? "no" : ""} onChange={e => setAnswer(q.key, e.target.value === "" ? null : e.target.value === "yes")}><option value="">Choose</option><option value="yes">Yes</option><option value="no">No</option></select> :
                  q.type === "single_choice" ? <select {...p} value={(a as string) ?? ""} onChange={e => setAnswer(q.key, e.target.value || null)}><option value="">Choose</option>{choices(q).map(c => <option key={c.value} value={c.value}>{c.label}</option>)}</select> :
                  q.type === "multi_choice" ? <select {...p} multiple size={4} value={(a as string[]) ?? []} onChange={e => setAnswer(q.key, Array.from(e.target.selectedOptions, o => o.value))}>{choices(q).map(c => <option key={c.value} value={c.value}>{c.label}</option>)}</select> :
                  <input {...p} type="file" onChange={e => { const f = e.target.files?.[0]; if (f) void upload(f, async attachment => setAnswer(q.key, attachment)); }} />}
              </ReqField>;
            })}</div>}
        </section>
      </div>
      <aside className="req-side">
        <section className="req-card" aria-labelledby="vr-docs-title" id="vr-docs" tabIndex={-1}><header className="req-card-head"><h2 id="vr-docs-title">Documents</h2></header>
          {docList.error ? <div role="alert" className="req-notice"><p>{docList.error}</p><button type="button" onClick={docList.reload}>Try again</button></div> : (docList.data ?? []).length === 0 ? <p>{saved ? "No document types are required." : "Save the draft to attach documents."}</p> :
            <ul className="vendor-list-plain">{(docList.data ?? []).map(d => <li key={d.type_code}>
              <strong>{d.type_code}</strong>{d.required ? " · required" : ""}
              <Signal tone={d.state === "missing" ? "danger" : "success"} label={d.state === "missing" ? "Missing" : "Attached"} />
              <label className="req-link-button"><UploadSimple aria-hidden="true" weight="regular" />Upload {d.type_code}
                <input type="file" className="visually-hidden" aria-label={`Upload ${d.type_code}`} onChange={e => { const f = e.target.files?.[0]; if (f) void addDocument(d.type_code, f); e.target.value = ""; }} /></label>
            </li>)}</ul>}
        </section>
      </aside></div>
    </fieldset>
    {locked ? <div className="req-action-bar">
      <p role="status" className="req-status">{status}</p>
      {saved && <a className="form-submit" href={`/vendors/${saved.id}`} onClick={e => { e.preventDefault(); navigate(`/vendors/${saved.id}`); }}>View vendor</a>}
    </div> : <div className="req-action-bar">
      <p role="status" className="req-status">{busy === "submit" ? "Submitting" : busy === "save" ? "Saving" : dirty && saved ? `${status} · unsaved changes` : status}</p>
      <p className="req-required-state">{complete(form, questions) ? <span><CheckCircle aria-hidden="true" weight="regular" />All required fields complete</span> : <span>Required fields are still empty</span>}</p>
      <button type="button" onClick={() => void saveDraft()} aria-busy={busy === "save" || undefined}><FloppyDisk aria-hidden="true" weight="regular" />Save Draft</button>
      <button type="button" className="form-submit" onClick={() => void submit()} aria-busy={busy === "submit" || undefined}><PaperPlaneTilt aria-hidden="true" weight="regular" />{busy === "submit" ? "Submitting" : "Submit for Approval"}</button>
    </div>}
  </section>;
}
