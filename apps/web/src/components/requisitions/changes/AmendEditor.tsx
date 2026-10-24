import { FloppyDisk, ListMagnifyingGlass, Trash } from "@phosphor-icons/react";
import * as Dialog from "@radix-ui/react-dialog";
import { useEffect, useRef, useState } from "react";
import { apiClient } from "../../../api/client";
import { registerGuard } from "../../../lib/navigation-guard";
import { CardSkeleton } from "../DetailCards";
import LinesCard from "../LinesCard";
import ReqField from "../ReqField";
import SubmitSummary from "../SubmitSummary";
import { failure, lookup, mutationHeaders, type LookupOption, type Problem, type Requisition } from "../api";
import { estimateOf, lineBody, localToday, problemFromError, type DraftLine, type SummaryProblem } from "../model";
import { useLoad } from "../useLoad";
import type { components } from "../../../api/generated/schema";

type Amendment = components["schemas"]["AmendmentRead"];
type FormHeader = { title: string; justification: string; needed_by: string; department_code: string; ledger_account_code: string; delivery_address_id: string };

const headerOf = (a: Amendment): FormHeader => ({ title: a.header.title, justification: a.header.justification ?? "", needed_by: a.header.needed_by ?? "", department_code: a.header.department_code ?? "", ledger_account_code: a.header.ledger_account_code ?? "", delivery_address_id: a.header.delivery_address_id ?? "" });
const linesOf = (a: Amendment): DraftLine[] => a.lines.map(l => ({ key: l.line_id, line_id: l.line_id, line_type: l.line_type, description: l.description, quantity: l.quantity, uom_code: l.uom_code, unit_price: l.unit_price, catalogue_item_id: l.catalogue_item_id, category_code: l.category_code }));
const snapshot = (h: FormHeader, l: DraftLine[]) => JSON.stringify([h, l.map(({ key, ...rest }) => (void key, rest))]);
const text = (v: string) => v.trim() === "" ? null : v.trim();

const withCurrent = (options: LookupOption[] | null, code: string): LookupOption[] => {
  const list = options ?? [];
  return code && !list.some(o => o.value === code) ? [...list, { value: code, label: `${code} (inactive)`, disabled: false, note: null }] : list;
};

/** `/amend`: opens the amendment (or continues the open one), edits its values, saves with If-Match, then goes to review. */
export default function AmendEditor({ id, navigate, onLoaded }: { id: string; navigate: (href: string) => void; onLoaded?: (title: string) => void }) {
  const startKey = useRef(crypto.randomUUID());
  const loaded = useLoad(async () => {
    const got = await apiClient.GET("/api/v1/requisitions/{id}", { params: { path: { id } } });
    if (!got.data) throw new Error(failure(got.error, "Loading this requisition"));
    onLoaded?.(got.data.title);
    const row: Requisition = got.data;
    if (row.amendment && row.amendment.status !== "draft") return { row, amendment: null };
    // Start is a POST; one key per page visit, so a repeated load replays instead of opening a second amendment.
    const opened = row.amendment
      ? await apiClient.GET("/api/v1/requisitions/{id}/amendments/current", { params: { path: { id } } })
      : await apiClient.POST("/api/v1/requisitions/{id}/amendments", { params: { path: { id } }, headers: mutationHeaders(startKey.current, row.version) });
    if (!opened.data) throw new Error(failure(opened.error as Problem | undefined, "Opening the amendment"));
    return { row, amendment: opened.data };
  }, [id]);
  if (loaded.error) return <section className="req-page" role="alert"><h2>Could not open the amendment</h2><p>{loaded.error}</p>
    <button type="button" onClick={loaded.reload}>Try again</button> <a href={`/requisitions/${id}`} onClick={e => { e.preventDefault(); navigate(`/requisitions/${id}`); }}>Back to the requisition</a></section>;
  if (!loaded.data) return <section className="req-page" aria-busy="true" aria-label="Amendment, loading"><div className="req-grid"><div className="req-main"><CardSkeleton name="the details" /><CardSkeleton name="the lines" /></div><aside className="req-side"><CardSkeleton name="the project" /></aside></div></section>;
  if (!loaded.data.amendment) return <section className="req-page"><h2>Amendment pending approval</h2><p>This amendment has been sent and cannot be edited. The approved version stays in force until it is approved.</p>
    <a href={`/requisitions/${id}`} onClick={e => { e.preventDefault(); navigate(`/requisitions/${id}`); }}>Back to the requisition</a></section>;
  return <Editor key={loaded.data.amendment.revision_no} id={id} row={loaded.data.row} initial={loaded.data.amendment} navigate={navigate} />;
}

function Editor({ id, row, initial, navigate }: { id: string; row: Requisition; initial: Amendment; navigate: (href: string) => void }) {
  const [header, setHeader] = useState(() => headerOf(initial));
  const [lines, setLines] = useState(() => linesOf(initial));
  const [version, setVersion] = useState(initial.version);
  const [saved, setSaved] = useState(() => snapshot(headerOf(initial), linesOf(initial)));
  const [summary, setSummary] = useState<{ title: string; lead: string; problems: SummaryProblem[]; detail?: string } | null>(null);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<"save" | "discard" | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [asking, setAsking] = useState(false);
  const keep = useRef<HTMLButtonElement>(null);
  const addRef = useRef<HTMLButtonElement>(null);
  const key = useRef<string | null>(null);

  const dirty = snapshot(header, lines) !== saved;
  const dirtyRef = useRef(dirty);
  useEffect(() => { dirtyRef.current = dirty; }, [dirty]);
  // UX-003: the shared guard module, registered only while there is something to lose.
  useEffect(() => dirty ? registerGuard(() => dirtyRef.current) : undefined, [dirty]);

  const departments = useLoad(() => lookup("department"), []);
  const ledger = useLoad(() => lookup("ledger_account"), []);
  const uoms = useLoad(() => lookup("uom"), []);
  const addresses = useLoad(() => lookup("delivery_address", row.bu_id), [row.bu_id]);
  const listError = [departments, ledger, uoms, addresses].find(l => l.error)?.error ?? null;

  const set = (patch: Partial<FormHeader>) => { key.current = null; setHeader(h => ({ ...h, ...patch })); };
  const editLines: typeof setLines = value => { key.current = null; setLines(value); };
  const show = (error: Problem | undefined, title: string) => {
    const list = (error?.errors ?? []).map(problemFromError);
    setSummary({ title: list.length ? `${title}. Fix ${list.length} ${list.length === 1 ? "thing" : "things"}` : title, lead: "Everything you entered is kept.", problems: list, detail: list.length ? undefined : failure(error, "Saving the amendment") });
    setErrors(Object.fromEntries(list.map(p => [p.target, p.message])));
  };

  /** PUT with If-Match; true when saved. A stale version says so and keeps what was typed. */
  const save = async (): Promise<boolean> => {
    if (busy) return false;
    setBusy("save"); setSummary(null); setErrors({}); setToast(null);
    key.current ??= crypto.randomUUID();
    try {
      const { data, error } = await apiClient.PUT("/api/v1/requisitions/{id}/amendments/{revision_no}", {
        params: { path: { id, revision_no: initial.revision_no } }, headers: mutationHeaders(key.current, version),
        body: { header: { title: header.title.trim(), justification: text(header.justification), needed_by: text(header.needed_by), department_code: text(header.department_code), ledger_account_code: text(header.ledger_account_code), delivery_address_id: text(header.delivery_address_id) }, lines: lines.map(lineBody) },
      });
      if (!data) {
        const problem = error as Problem | undefined;
        if (problem?.checks?.problem === "stale_version") setSummary({ title: "Not saved", lead: "This requisition changed since you opened it. Reload to see the current version. Everything you entered is kept.", problems: [] });
        else show(problem, "Not saved");
        return false;
      }
      key.current = null;
      const fresh = data as Amendment;
      setVersion(fresh.version);
      const nextLines = linesOf(fresh).map((l, i) => ({ ...l, key: lines[i]?.key ?? l.key }));
      setLines(nextLines);
      setSaved(snapshot(header, nextLines));
      setToast("Amendment saved. The approved version stays in force until the amendment is approved.");
      return true;
    } catch {
      setSummary({ title: "Not saved", lead: "Everything you entered is kept.", problems: [], detail: failure(undefined, "Saving the amendment") });
      return false;
    } finally { setBusy(null); }
  };

  const review = async () => {
    if (dirty && !(await save())) return;
    dirtyRef.current = false; // saved: leaving must not ask
    navigate(`/requisitions/${id}/amend/review`);
  };

  const discard = async () => {
    setBusy("discard");
    try {
      const { response, error } = await apiClient.DELETE("/api/v1/requisitions/{id}/amendments/{revision_no}", { params: { path: { id, revision_no: initial.revision_no } }, headers: mutationHeaders(crypto.randomUUID()) });
      if (response.ok) { dirtyRef.current = false; navigate(`/requisitions/${id}`); return; }
      setAsking(false); show(error as Problem | undefined, "Not discarded");
    } catch { setAsking(false); setSummary({ title: "Not discarded", lead: "Your amendment is kept.", problems: [], detail: failure(undefined, "Discarding the amendment") }); }
    finally { setBusy(null); }
  };

  const estimate = estimateOf(lines, initial.currency);
  const err = (target: string) => errors[target];
  return <section className="req-page" aria-label="Amendment form">
    <header className="req-heading">
      <p>{row.number} · Amendment, version {initial.revision_no} · amounts in {initial.currency}</p>
      <a href={`/requisitions/${id}`} onClick={e => { e.preventDefault(); navigate(`/requisitions/${id}`); }}>Back to requisition</a>
    </header>
    <p className="req-note">The approved version stays in force until this amendment is approved or applied.</p>
    {summary && <SubmitSummary title={summary.title} lead={summary.lead} problems={summary.problems} detail={summary.detail} />}
    {listError && <div role="alert" className="req-notice"><p>{listError}</p><button type="button" onClick={() => { for (const l of [departments, ledger, uoms, addresses]) if (l.error) l.reload(); }}>Try again</button></div>}
    <div className="req-grid">
      <div className="req-main">
        <section className="req-card" id="requisition-details" tabIndex={-1} aria-labelledby="amend-details-title">
          <header className="req-card-head"><h2 id="amend-details-title">Amendment Details</h2></header>
          <div className="req-fields">
            <ReqField id="req-title" label="Title" wide help="A short name approvers will recognise." error={err("req-title")}>
              {p => <input {...p} type="text" maxLength={200} value={header.title} onChange={e => set({ title: e.target.value })} />}</ReqField>
            <div className="req-field"><span className="t-label" id="amend-project-label">Project</span>
              <output aria-labelledby="amend-project-label" className="req-readonly">{row.project_name ? `${row.project_number} ${row.project_name}` : "Standalone request"}</output>
              <p className="field-help">The project cannot change in an amendment. Withdraw this requisition and raise a new one to move it.</p></div>
            <ReqField id="req-department_code" label="Department" help="The department that bears the cost." error={err("req-department_code")}>
              {p => <select {...p} value={header.department_code} onChange={e => set({ department_code: e.target.value })}><option value="">Choose a department</option>
                {withCurrent(departments.data, header.department_code).map(o => <option key={o.value} value={o.value}>{o.label}</option>)}</select>}</ReqField>
            <ReqField id="req-ledger_account_code" label="Ledger account" help="Where the spend is booked. Changing it is a material change." error={err("req-ledger_account_code")}>
              {p => <select {...p} value={header.ledger_account_code} onChange={e => set({ ledger_account_code: e.target.value })}><option value="">Choose a ledger account</option>
                {withCurrent(ledger.data, header.ledger_account_code).map(o => <option key={o.value} value={o.value}>{o.label}</option>)}</select>}</ReqField>
            <ReqField id="req-needed_by" label="Needed by" help="The date the goods or service must arrive." error={err("req-needed_by")}>
              {p => <input {...p} type="date" min={localToday()} value={header.needed_by} onChange={e => set({ needed_by: e.target.value })} />}</ReqField>
            <ReqField id="req-delivery_address_id" label="Delivery location" help="Where it is delivered." error={err("req-delivery_address_id")}>
              {p => <select {...p} value={header.delivery_address_id} onChange={e => set({ delivery_address_id: e.target.value })}><option value="">Choose a delivery location</option>
                {withCurrent(addresses.data, header.delivery_address_id).map(o => <option key={o.value} value={o.value}>{o.label}</option>)}</select>}</ReqField>
            <ReqField id="req-justification" label="Justification" wide help="Why the change is needed, in plain words an approver can act on." error={err("req-justification")}>
              {p => <textarea {...p} rows={3} maxLength={4000} value={header.justification} onChange={e => set({ justification: e.target.value })} />}</ReqField>
          </div>
        </section>
        <LinesCard lines={lines} currency={initial.currency} units={uoms.data ?? []} errors={errors} onChange={editLines} addRef={addRef} />
      </div>
      <aside className="req-side">
        <section className="req-card" aria-labelledby="amend-total-title"><header className="req-card-head"><h2 id="amend-total-title">Estimate</h2></header>
          <p>Estimated total in {initial.currency}: {estimate}. The funding effect is shown on the next screen, from the server.</p></section>
      </aside>
    </div>
    <div className="req-action-bar">
      <p role="status" className="req-status">{busy === "save" ? "Saving" : dirty ? "Unsaved changes" : "Saved"}</p>
      <button type="button" className="req-destructive" onClick={() => setAsking(true)}><Trash aria-hidden="true" weight="regular" />Discard Amendment</button>
      <button type="button" onClick={() => void save()} aria-busy={busy === "save" || undefined}><FloppyDisk aria-hidden="true" weight="regular" />Save</button>
      <button type="button" className="form-submit" onClick={() => void review()} aria-busy={busy === "save" || undefined}><ListMagnifyingGlass aria-hidden="true" weight="regular" />Review Changes</button>
    </div>
    {toast && <p className="req-toast" role="status">{toast}</p>}
    <Dialog.Root open={asking} onOpenChange={next => { if (busy === "discard" && !next) return; setAsking(next); }}>
      <Dialog.Portal><Dialog.Overlay className="project-dialog-overlay" />
        <Dialog.Content role="alertdialog" className="project-dialog material-surface material-shadow req-dialog" data-shadow="lg" onOpenAutoFocus={e => { e.preventDefault(); keep.current?.focus(); }}>
          <Dialog.Title>Discard this amendment?</Dialog.Title>
          <Dialog.Description>Your edits to the amendment are lost. The approved version of {row.number} is not affected.</Dialog.Description>
          <div className="req-actions">
            <button type="button" ref={keep} onClick={() => setAsking(false)} disabled={busy === "discard"}>Keep Amendment</button>
            <button type="button" className="req-destructive" onClick={() => void discard()} aria-busy={busy === "discard" || undefined}>Discard Amendment</button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  </section>;
}
