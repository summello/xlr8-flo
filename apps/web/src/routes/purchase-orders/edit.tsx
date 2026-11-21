import { useEffect, useRef, useState } from "react";
import { apiClient } from "../../api/client";
import ErrorSummary, { type SummaryError } from "../../components/form/ErrorSummary";
import { BudgetPreview, LinesEditor, OrderCard, TotalsBar, VendorCard, lineId, type Errors } from "../../components/purchasing/editor/Cards";
import { initial, patchBody, type Edited } from "../../components/purchasing/editor/patch";
import VendorFileMarks from "../../components/purchasing/editor/VendorFileMarks";
import { NotesAndFiles } from "../../components/purchasing/PoCards";
import type { Po } from "../../components/purchasing/api";
import { failure, type Problem } from "../../components/requisitions/api";
import { useLoad } from "../../components/requisitions/useLoad";
import { writeHeaders } from "../../features/auth/api";
import { registerGuard } from "../../lib/navigation-guard";
import "../../styles/projects.css";
import "../../styles/requisitions.css";
import "../../styles/vendors.css";
import "../../styles/sourcing.css";
import "../../styles/purchase-orders.css";

type Nav = (href: string) => void;
type Detailed = Problem & { errors?: { field: string; message: string }[] | null; checks?: Record<string, unknown> | null };

const HEADER_IDS: Record<string, [string, string]> = {
  ship_to_address_id: ["po-ship-to", "Ship To"], terms: ["po-terms", "Terms"], needed_by: ["po-needed-by", "Needed by"], document_freight: ["po-document-freight", "Document freight"],
};
const LINE_LABEL = { quantity: "Quantity", tax: "Tax", freight: "Freight" } as const;

/** 422 `errors[].field` to a control id; whatever has no control lands on the Save button so the summary link still works. */
function mapErrors(error: Detailed | undefined): { errors: Errors; summary: SummaryError[] } {
  const errors: Errors = {}, summary: SummaryError[] = [];
  const add = (fieldId: string, label: string, message: string) => { if (!errors[fieldId]) { errors[fieldId] = message; summary.push({ fieldId, label, message }); } };
  for (const e of error?.errors ?? []) {
    const message = e.message.replace(/^Value error, /, "");
    const line = /^lines\/(\d+)\/(quantity|tax|freight)$/.exec(e.field);
    const header = HEADER_IDS[e.field];
    if (line) { const key = line[2] as keyof typeof LINE_LABEL; add(lineId(Number(line[1]), key), `Line ${line[1]} ${LINE_LABEL[key]}`, message); }
    else if (header) add(header[0], header[1], message);
    else if (e.field === "lines") add("po-lines", "Lines", message);
    else add("po-save-button", "Order", message);
  }
  if (summary.length === 0) add("po-save-button", "Order", failure(error, "Saving the order"));
  return { errors, summary };
}

function Editor({ po, navigate }: { po: Po; navigate: Nav }) {
  const [saved, setSaved] = useState(po);
  const [edited, setEdited] = useState(() => initial(po));
  const [errors, setErrors] = useState<Errors>({});
  const [summary, setSummary] = useState<SummaryError[]>([]);
  const [message, setMessage] = useState<string | null>(null);
  const [stale, setStale] = useState(false);
  const [locked, setLocked] = useState(false);
  const [busy, setBusy] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const [projects, setProjects] = useState(new Map<string, string>());
  const dirty = Object.keys(patchBody(saved, edited)).length > 0;
  const live = useRef({ dirty, leaving: false });
  useEffect(() => { live.current.dirty = dirty; });
  useEffect(() => registerGuard(() => live.current.dirty && !live.current.leaving), []);

  const set = (patch: Partial<Edited>) => setEdited(e => ({ ...e, ...patch }));

  /** PATCH what changed. True when the saved order now matches the form (nothing to send counts). */
  const save = async (): Promise<boolean> => {
    setErrors({}); setSummary([]); setMessage(null); setStale(false);
    const body = patchBody(saved, edited);
    if (Object.keys(body).length === 0) return true;
    try {
      const { data, error, response } = await apiClient.PATCH("/api/v1/purchase-orders/{id}", { params: { path: { id: saved.id } }, headers: { ...writeHeaders(), "If-Match": String(saved.version) }, body });
      if (data) { setSaved(data); setEdited(initial(data)); setRefresh(n => n + 1); setMessage("Saved."); return true; }
      const problem = error as Detailed | undefined;
      if (response.status === 422) { const mapped = mapErrors(problem); setErrors(mapped.errors); setSummary(mapped.summary); }
      else if (response.status === 409 && problem?.checks?.problem === "stale_version") { setStale(true); setMessage("Someone saved this order after you opened it. Your edits are still here; reload to see theirs (reloading discards yours)."); }
      else if (response.status === 409 && problem?.checks?.problem === "po_not_draft") setLocked(true);
      else setMessage(failure(problem, "Saving the order"));
    } catch { setMessage(failure(undefined, "Saving the order")); }
    return false;
  };

  const submit = async () => {
    setBusy(true);
    try {
      if (dirty && !(await save())) return; // what is approved is what was saved
      const { error, response } = await apiClient.POST("/api/v1/purchase-orders/{id}/submit", { params: { path: { id: saved.id } }, headers: writeHeaders() });
      if (response.ok) { live.current.leaving = true; navigate(`/purchase-orders/${saved.id}`); return; }
      const problem = error as Detailed | undefined;
      const why = problem?.checks?.problem;
      if (response.status === 409 && why === "workflow_not_set_up") setMessage("No purchase order workflow is published. Ask an administrator to set one up. Your draft is kept.");
      else if (response.status === 409 && why === "vendor_not_eligible") setMessage(`This vendor is no longer eligible: ${Array.isArray(problem?.checks?.reasons) ? (problem.checks.reasons as unknown[]).join("; ") : "see the vendor"}. Your draft is kept.`);
      else if (response.status === 403) setMessage(problem?.detail ?? failure(problem, "Submitting the order"));
      else setMessage(`${problem?.detail ?? "Submitting failed."} Your draft is kept.`);
    } catch { setMessage(failure(undefined, "Submitting the order")); }
    finally { setBusy(false); }
  };

  if (locked) return <section className="req-page" aria-label="Edit purchase order"><p>This purchase order can no longer be edited here.</p><a href={`/purchase-orders/${saved.id}`} onClick={e => { e.preventDefault(); navigate(`/purchase-orders/${saved.id}`); }}>Back to the purchase order</a></section>;
  return <section className="req-page vendor-profile po-page" aria-label="Edit purchase order">
    <header className="vendor-head"><div><h2 className="vendor-name po-number">{saved.number}</h2><p className="req-sub">Draft · version {saved.version}</p></div></header>
    <ErrorSummary errors={summary} />
    {message && <p role={stale || message !== "Saved." ? "alert" : "status"}>{message}{stale && <> <button type="button" onClick={() => window.location.reload()}>Reload</button></>}</p>}
    <div className="po-body">
      <div className="po-col">
        <OrderCard po={saved} edited={edited} set={set} errors={errors} />
        <LinesEditor po={saved} edited={edited} set={set} errors={errors} projects={projects} />
        <TotalsBar po={saved} unsaved={dirty} />
        <NotesAndFiles id={saved.id} onUploaded={() => setRefresh(n => n + 1)}><VendorFileMarks id={saved.id} refreshKey={refresh} /></NotesAndFiles>
      </div>
      <div className="po-col">
        <VendorCard po={saved} />
        <BudgetPreview po={saved} refreshKey={refresh} onProjects={setProjects} />
        <section className="req-card po-actions" aria-label="Actions">
          <button type="button" id="po-save-button" aria-busy={busy || undefined} onClick={() => void save()}>Save</button>
          {(saved.actions ?? []).includes("submit")
            ? <button type="button" aria-busy={busy || undefined} onClick={() => void submit()}>Submit for Approval</button>
            : <p className="req-sub">Only the buyer who drafted this order can submit it.</p>}
        </section>
      </div>
    </div>
  </section>;
}

export default function EditPurchaseOrder({ id, navigate }: { id: string; navigate: Nav }) {
  const generation = useRef(0);
  const po = useLoad(async () => {
    const { data, error, response } = await apiClient.GET("/api/v1/purchase-orders/{id}", { params: { path: { id } } });
    if (!data) throw new Error(response?.status === 404 ? "This purchase order does not exist or you cannot see it." : failure(error as Problem | undefined, "Loading the purchase order"));
    return { po: data, generation: ++generation.current };
  }, [id]);
  useEffect(() => { if (po.data) document.title = `Edit PO ${po.data.po.number}`; }, [po.data]);
  const back = <a href={`/purchase-orders/${id}`} onClick={e => { e.preventDefault(); navigate(`/purchase-orders/${id}`); }}>Back to the purchase order</a>;
  if (po.error) return <section className="req-page" aria-label="Edit purchase order"><div role="alert"><p>{po.error}</p><button type="button" onClick={po.reload}>Try again</button></div>{back}</section>;
  if (!po.data) return <section className="req-page" aria-label="Edit purchase order"><div className="req-card req-card-skeleton" aria-busy="true"><p className="req-skeleton" role="status" aria-label="Loading purchase order" /></div></section>;
  const p = po.data.po;
  if (p.status !== "draft" || !(p.actions ?? []).includes("edit")) return <section className="req-page" aria-label="Edit purchase order"><p>This purchase order can no longer be edited here.</p>{back}</section>;
  return <Editor key={po.data.generation} po={p} navigate={navigate} />;
}
