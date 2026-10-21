import { CheckCircle, FloppyDisk, PaperPlaneTilt } from "@phosphor-icons/react";
import { useCallback, useEffect, useRef, useState } from "react";
import { apiClient } from "../../api/client";
import { registerGuard } from "../../lib/navigation-guard";
import StatusPill from "../status/StatusPill";
import Money from "./ReqMoney";
import Attachments from "./Attachments";
import Catalogue from "./Catalogue";
import FundingCheck from "./FundingCheck";
import LinesCard from "./LinesCard";
import ReqField from "./ReqField";
import SubmitSummary from "./SubmitSummary";
import { failure, formatTime, mutationHeaders, type CatalogueItem, type LookupOption, type Problem, type Requisition, type RequisitionStatus, type RequisitionLine, type SubmissionCheck } from "./api";
import { EMPTY_HEADER, clientProblems, estimateOf, initialHeader, initialLines, lineBody, localToday, newLine, problemFromError, type DraftLine, type Header, type SummaryProblem } from "./model";
import { useLoad } from "./useLoad";

export type Initial = { row: Requisition; lines: RequisitionLine[] };
type Summary = { title: string; lead: string; problems: SummaryProblem[]; detail?: string };


async function lookup(kind: string, buId?: string): Promise<LookupOption[]> {
  const { data, error } = await apiClient.GET("/api/v1/requisitions/lookups/{kind}", { params: { path: { kind }, query: { limit: 50, ...(buId ? { bu_id: buId } : {}) } } });
  if (!data) throw new Error(failure(error, "Loading the list of choices"));
  return data.rows;
}
const optionLabel = (o: LookupOption) => { const notes = [o.note, o.codes_expired ? "expired code" : null].filter(Boolean); return notes.length ? `${o.label} (${notes.join(", ")})` : o.label; };

export default function RequisitionForm({ navigate, initial }: { navigate: (href: string) => void; initial?: Initial }) {
  const [header, setHeader] = useState<Header>(() => initial ? initialHeader(initial.row) : EMPTY_HEADER);
  const [lines, setLines] = useState<DraftLine[]>(() => initial ? initialLines(initial.lines) : []);
  const [row, setRow] = useState<Requisition | null>(initial?.row ?? null);
  const [version, setVersion] = useState<number | null>(initial?.row.version ?? null);
  const snapshot = (h: Header, l: DraftLine[]) => JSON.stringify([h, l.map(({ key, ...rest }) => (void key, rest))]);
  const [saved, setSaved] = useState(() => snapshot(header, lines));
  const [summary, setSummary] = useState<Summary | null>(null);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [status, setStatus] = useState(initial ? "Draft loaded" : "Not saved yet");
  const [busy, setBusy] = useState<"save" | "submit" | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [done, setDone] = useState<Requisition | null>(null);
  const [check, setCheck] = useState<SubmissionCheck | null>(null);
  const [checkError, setCheckError] = useState<string | null>(null);
  const [project, setProject] = useState<{ id: string; bu_id: string; currency: string } | null>(null);
  const key = useRef<string | null>(null);
  const addRef = useRef<HTMLButtonElement>(null);

  const dirty = done === null && snapshot(header, lines) !== saved;
  const dirtyRef = useRef(dirty);
  useEffect(() => { dirtyRef.current = dirty; }, [dirty]);
  // UX-003: the guard (and beforeunload) exists only while there is something to lose.
  useEffect(() => dirty ? registerGuard(() => dirtyRef.current) : undefined, [dirty]);

  const projectId = header.mode === "project" ? header.project_id : "";
  useEffect(() => {
    if (!projectId) return;
    let active = true;
    void apiClient.GET("/api/v1/projects/{id}", { params: { path: { id: projectId } } }).then(({ data }) => {
      if (active && data) setProject({ id: projectId, bu_id: data.bu_id, currency: data.currency });
    }).catch(() => { /* currency falls back to the saved requisition's */ });
    return () => { active = false; };
  }, [projectId]);
  const activeProject = project && project.id === projectId ? project : null;
  const buId = header.mode === "standalone" ? header.bu_id || null : activeProject?.bu_id ?? row?.bu_id ?? null;
  const currency = row?.currency ?? activeProject?.currency ?? null;
  const math = currency ?? "USD";

  const projects = useLoad(() => lookup("project"), []);
  const units = useLoad(() => lookup("bu"), []);
  const people = useLoad(() => lookup("user"), []);
  const departments = useLoad(() => lookup("department"), []);
  const ledger = useLoad(() => lookup("ledger_account"), []);
  const uoms = useLoad(() => lookup("uom"), []);
  const addresses = useLoad(() => lookup("delivery_address", buId ?? undefined), [buId]);
  const listError = [projects, units, people, departments, ledger, uoms, addresses].find(l => l.error)?.error ?? null;

  const refreshCheck = useCallback((id: string) => {
    apiClient.GET("/api/v1/requisitions/{id}/submission-check", { params: { path: { id } } }).then(({ data, error }) => {
      if (data) { setCheck(data); setCheckError(null); } else setCheckError(failure(error, "Checking funds"));
    }).catch(() => setCheckError(failure(undefined, "Checking funds")));
  }, []);
  useEffect(() => { if (initial) refreshCheck(initial.row.id); }, [initial, refreshCheck]);

  const estimate = estimateOf(lines, math);
  const errorFor = (target: string) => errors[target];
  const show = (s: Summary) => {
    setSummary(s);
    setErrors(Object.fromEntries(s.problems.map(p => [p.target, p.message])));
  };
  const problemSummary = (error: Problem | undefined, verb: "save" | "submit"): Summary => {
    const list = (error?.errors ?? []).map(problemFromError);
    if (list.length > 0) return { title: verb === "submit" ? `Fix ${list.length} ${list.length === 1 ? "thing" : "things"} before you submit` : `Not saved. Fix ${list.length} ${list.length === 1 ? "thing" : "things"}`,
      lead: verb === "submit" ? "Nothing was submitted. Everything you entered is kept." : "Everything you entered is kept.", problems: list };
    return { title: verb === "submit" ? "Not submitted" : "Not saved", lead: "Everything you entered is kept.", problems: [], detail: failure(error, verb === "submit" ? "Submitting" : "Saving the draft") };
  };

  const text = (v: string) => v.trim() === "" ? undefined : v.trim();
  /** Create or patch the header, then replace the lines atomically; each call carries the version the last returned. */
  const persist = async (action: string): Promise<Requisition | null> => {
    let current = row; let v = version;
    const fields = { justification: text(header.justification), needed_by: text(header.needed_by), department_code: text(header.department_code), ledger_account_code: text(header.ledger_account_code), delivery_address_id: text(header.delivery_address_id) };
    if (!current || v === null) {
      const created = await apiClient.POST("/api/v1/requisitions", { headers: mutationHeaders(`${action}-create`), body: { title: header.title.trim(), ...(header.mode === "project" ? { project_id: header.project_id } : { bu_id: header.bu_id, sponsor_ids: header.sponsor_ids }), ...fields } });
      if (!created.data) { show(problemSummary(created.error as Problem, "save")); return null; }
      current = created.data; v = created.data.version; setRow(current); setVersion(v);
      window.history.replaceState(null, "", `/requisitions/${current.id}/edit`);
    } else {
      const nulls = Object.fromEntries(Object.entries(fields).map(([k, val]) => [k, val ?? null]));
      const patched = await apiClient.PATCH("/api/v1/requisitions/{id}", { params: { path: { id: current.id } }, headers: mutationHeaders(`${action}-patch-${v}`, v), body: { title: header.title.trim() || undefined, ...(header.mode === "project" && header.project_id ? { project_id: header.project_id } : {}), ...(header.mode === "standalone" && header.sponsor_ids.length > 0 ? { sponsor_ids: header.sponsor_ids } : {}), ...nulls } });
      if (!patched.data) { show(problemSummary(patched.error as Problem, "save")); return null; }
      current = patched.data; v = patched.data.version; setRow(current); setVersion(v);
    }
    const replaced = await apiClient.PUT("/api/v1/requisitions/{id}/lines", { params: { path: { id: current.id } }, headers: mutationHeaders(`${action}-lines-${v}`, v), body: { lines: lines.map(lineBody) } });
    if (!replaced.data) { show(problemSummary(replaced.error as Problem, "save")); return null; }
    const next = { ...current, version: replaced.data.version, estimate: replaced.data.estimate, currency: replaced.data.currency };
    setRow(next); setVersion(replaced.data.version);
    setLines(initialLines(replaced.data.rows.map(r => r)).map((l, i) => ({ ...l, key: lines[i]?.key ?? l.key })));
    setSaved(snapshot(header, initialLines(replaced.data.rows).map((l, i) => ({ ...l, key: lines[i]?.key ?? l.key }))));
    refreshCheck(next.id);
    return next;
  };

  const saveDraft = async () => {
    if (busy) return;
    setBusy("save"); setSummary(null); setErrors({}); setToast(null);
    if (!header.title.trim() || (header.mode === "project" ? !header.project_id : !header.bu_id)) {
      const p = clientProblems(header, lines, localToday()).filter(x => ["req-title", "req-project_id", "req-bu_id"].includes(x.target));
      show({ title: `Not saved. Fix ${p.length} ${p.length === 1 ? "thing" : "things"}`, lead: "Everything you entered is kept.", problems: p });
      setBusy(null); return;
    }
    const result = await persist(crypto.randomUUID());
    if (result) { setStatus(`Draft saved ${formatTime(new Date())}`); setToast("Draft saved. Nothing is reserved until you submit."); }
    setBusy(null);
  };

  const submit = async () => {
    if (busy) return;
    setBusy("submit"); setSummary(null); setErrors({}); setToast(null);
    const local = clientProblems(header, lines, localToday());
    if (local.length > 0) {
      show({ title: `Fix ${local.length} ${local.length === 1 ? "thing" : "things"} before you submit`, lead: "Nothing was submitted. Everything you entered is kept.", problems: local });
      setBusy(null); return;
    }
    key.current ??= crypto.randomUUID(); // once per click, reused on retry; cleared when the form changes
    // A retry after a failed transition has nothing new to save: skip the save so the version and key stay put.
    const saved1 = row && !dirty ? row : await persist(key.current);
    if (!saved1) { setBusy(null); return; }
    const result = await apiClient.POST("/api/v1/requisitions/{id}/transitions", { params: { path: { id: saved1.id } }, headers: mutationHeaders(`${key.current}-submit`, saved1.version), body: { to: "approval_pending" } });
    if (!result.data) {
      const problem = result.error as Problem | undefined;
      show(problemSummary(problem, "submit"));
      refreshCheck(saved1.id);
      setBusy(null); return;
    }
    key.current = null;
    setRow(result.data); setVersion(result.data.version); setDone(result.data);
    setBusy(null);
  };

  const route = useLoad(async () => {
    if (!done?.approval) return null;
    const { data } = await apiClient.GET("/api/v1/approvals/instances/{id}", { params: { path: { id: done.approval.instance_id } } });
    return data ?? null;
  }, [done?.approval?.instance_id]);

  if (done) {
    const first = route.data?.route[0];
    return <section className="req-page req-confirmation" aria-labelledby="req-done-title">
      <div className="req-card">
        <StatusPill docType="requisition" status={done.status as RequisitionStatus} />
        <h2 id="req-done-title" tabIndex={-1} ref={el => el?.focus()}>{done.number} Submitted for Approval</h2>
        <p role="status">{done.reservation ? <>Reserved <Money value={done.reservation.remaining} currency={done.reservation.currency} /> against {done.project_name ?? "the project"}. </> : null}
          {first ? `Routed to ${first.approver_emails.join(", ") || first.label}, step 1 of ${route.data?.route.length}. ` : ""}You can withdraw it while it is pending, and the reservation is released once.</p>
        <div className="req-actions">
          <a className="form-submit" href={`/requisitions/${done.id}`} onClick={e => { e.preventDefault(); navigate(`/requisitions/${done.id}`); }}>View requisition</a>
          <a className="req-link-button" href="/requisitions/new" onClick={e => { e.preventDefault(); window.location.assign("/requisitions/new"); }}>New requisition</a>
        </div>
      </div>
    </section>;
  }

  // An actual user edit invalidates the submit key; save normalization (persist) never does.
  const set = (patch: Partial<Header>) => { key.current = null; setHeader(h => ({ ...h, ...patch })); };
  const editLines: typeof setLines = value => { key.current = null; setLines(value); };
  const withCurrent = (options: LookupOption[] | null, code: string, kind: string): LookupOption[] => {
    const list = options ?? [];
    return code && !list.some(o => o.value === code) ? [...list, { value: code, label: `${code} (${kind === "unit" ? "unavailable" : "inactive"})`, disabled: false, note: null }] : list;
  };
  // The unit lookup lists only units where the caller holds project_request.create (D-M2-18).
  const canRequest = header.mode === "standalone" || (units.data?.length ?? 0) > 0;
  const isRequest = header.mode === "standalone";
  return <section className="req-page" aria-label={isRequest ? "Project Request form" : "Requisition form"}>
    <header className="req-heading">
      <p>{row ? `Draft ${row.number} · ${status} · amounts in ${row.currency}` : "Draft · not saved yet"}</p>
      <a href="/requisitions" onClick={e => { e.preventDefault(); navigate("/requisitions"); }}>Back to requisitions</a>
    </header>
    {summary && <SubmitSummary title={summary.title} lead={summary.lead} problems={summary.problems} detail={summary.detail} />}
    {listError && <div role="alert" className="req-notice"><p>{listError}</p><button type="button" onClick={() => { for (const l of [projects, units, people, departments, ledger, uoms, addresses]) if (l.error) l.reload(); }}>Try again</button></div>}
    <div className="req-grid">
      <div className="req-main">
        <section className="req-card" id="requisition-details" tabIndex={-1} aria-labelledby="details-title">
          <header className="req-card-head"><h2 id="details-title">{isRequest ? "Project Request Details" : "Requisition Details"}</h2></header>
          <fieldset className="req-mode" disabled={row !== null}>
            <legend>Raise a request for</legend>
            <label><input type="radio" name="mode" id="req-mode" checked={header.mode === "project"} onChange={() => set({ mode: "project", bu_id: "" })} />An existing project</label>
            {canRequest && <label><input type="radio" name="mode" checked={header.mode === "standalone"} onChange={() => set({ mode: "standalone", project_id: "" })} />A new Project Request</label>}
            {row !== null && <p className="field-help">The kind of request cannot change once the draft is saved.</p>}
          </fieldset>
          {isRequest && <p className="req-note">A Project Request asks for budget for a new project. Full approval creates the project unless the approver assigns the request to an existing one.</p>}
          <div className="req-fields">
            <ReqField id="req-title" label="Title" wide help="A short name approvers will recognise." error={errorFor("req-title")}>
              {p => <input {...p} type="text" maxLength={200} value={header.title} onChange={e => set({ title: e.target.value })} />}</ReqField>
            {header.mode === "project" ?
              <ReqField id="req-project_id" label="Project" help="The funding project this request draws on." error={errorFor("req-project_id")}>
                {p => <select {...p} value={header.project_id} onChange={e => set({ project_id: e.target.value })}><option value="">Choose a project</option>
                  {withCurrent(projects.data, header.project_id, "unit").map(o => <option key={o.value} value={o.value} disabled={o.disabled}>{optionLabel(o)}</option>)}</select>}</ReqField> :
              <ReqField id="req-bu_id" label="Business unit" help="The unit that will own the new project." error={errorFor("req-bu_id")}>
                {p => <select {...p} value={header.bu_id} onChange={e => set({ bu_id: e.target.value })}><option value="">Choose a business unit</option>
                  {withCurrent(units.data, header.bu_id, "unit").map(o => <option key={o.value} value={o.value} disabled={o.disabled}>{optionLabel(o)}</option>)}</select>}</ReqField>}
            {isRequest && <ReqField id="req-sponsor_ids" label="Sponsors" help="Choose one to ten people who sponsor the project. Hold Ctrl or Command to choose several." error={errorFor("req-sponsor_ids")}>
              {p => <select {...p} multiple size={5} value={header.sponsor_ids} onChange={e => set({ sponsor_ids: Array.from(e.target.selectedOptions, o => o.value).slice(0, 10) })}>
                {people.data?.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}</select>}</ReqField>}
            <ReqField id="req-department_code" label="Department" help="The department that bears the cost." error={errorFor("req-department_code")}>
              {p => <select {...p} value={header.department_code} onChange={e => set({ department_code: e.target.value })}><option value="">Choose a department</option>
                {withCurrent(departments.data, header.department_code, "dept").map(o => <option key={o.value} value={o.value}>{o.label}</option>)}</select>}</ReqField>
            <ReqField id="req-ledger_account_code" label="Ledger account" help="Where the spend is booked. An inactive account is flagged and must be replaced." error={errorFor("req-ledger_account_code")}>
              {p => <select {...p} value={header.ledger_account_code} onChange={e => set({ ledger_account_code: e.target.value })}><option value="">Choose a ledger account</option>
                {withCurrent(ledger.data, header.ledger_account_code, "ledger").map(o => <option key={o.value} value={o.value}>{o.label}</option>)}</select>}</ReqField>
            <ReqField id="req-needed_by" label="Needed by" help="The date the goods or service must arrive. It cannot be in the past." error={errorFor("req-needed_by")}>
              {p => <input {...p} type="date" min={localToday()} value={header.needed_by} onChange={e => set({ needed_by: e.target.value })} />}</ReqField>
            <ReqField id="req-delivery_address_id" label="Delivery location" help="Where it is delivered. Choose a project or unit first to see its locations." error={errorFor("req-delivery_address_id")}>
              {p => <select {...p} value={header.delivery_address_id} onChange={e => set({ delivery_address_id: e.target.value })}><option value="">Choose a delivery location</option>
                {withCurrent(addresses.data, header.delivery_address_id, "unit").map(o => <option key={o.value} value={o.value}>{o.label}</option>)}</select>}</ReqField>
            <div className="req-field"><span className="t-label" id="req-currency-label">Currency</span><output aria-labelledby="req-currency-label" className="req-readonly">{currency ?? "Set when saved"}</output><p className="field-help">Taken from the project; a standalone request uses the organization's base currency.</p></div>
            <div className="req-field"><span className="t-label" id="req-requester-label">Requestor</span><output aria-labelledby="req-requester-label" className="req-readonly">{row?.requester_email ?? "You"}</output><p className="field-help">The signed-in user raising this request.</p></div>
            <ReqField id="req-justification" label="Justification" wide help="Why this is needed, in plain words an approver can act on." error={errorFor("req-justification")}>
              {p => <textarea {...p} rows={3} maxLength={4000} value={header.justification} onChange={e => set({ justification: e.target.value })} />}</ReqField>
          </div>
        </section>
        <LinesCard lines={lines} currency={math} units={uoms.data ?? []} errors={errors} onChange={editLines} addRef={addRef} />
      </div>
      <aside className="req-side">
        <FundingCheck check={check} estimate={estimate} currency={math} standalone={header.mode === "standalone"} saved={row !== null} error={checkError} retry={() => row && refreshCheck(row.id)} />
        <Catalogue buId={buId} onAdd={(item: CatalogueItem) => editLines(l => [...l, { ...newLine(item.line_type), description: item.description, uom_code: item.uom_code, quantity: "1", unit_price: item.last_unit_price, catalogue_item_id: item.id, category_code: item.category_code }])} />
        <Attachments id={row?.id ?? null} />
      </aside>
    </div>
    <div className="req-action-bar">
      <p role="status" className="req-status">{busy === "submit" ? "Submitting" : busy === "save" ? "Saving" : dirty && row ? `${status} · unsaved changes` : status}</p>
      <p className="req-required-state"><span className="rq-open">Required fields are still empty</span><span className="rq-all"><CheckCircle aria-hidden="true" weight="regular" />All required fields complete</span></p>
      <button type="button" onClick={() => void saveDraft()} disabled={busy === "save"} aria-busy={busy === "save" || undefined}><FloppyDisk aria-hidden="true" weight="regular" />Save draft</button>
      <button type="button" className="form-submit" onClick={() => void submit()} disabled={busy === "submit"} aria-busy={busy === "submit" || undefined}><PaperPlaneTilt aria-hidden="true" weight="regular" />{busy === "submit" ? "Submitting" : "Submit for approval"}</button>
    </div>
    {toast && <p className="req-toast" role="status">{toast}</p>}
  </section>;
}
