import { Plus } from "@phosphor-icons/react";
import { useEffect, useRef, useState } from "react";
import { apiClient } from "../../api/client";
import ErrorSummary, { type SummaryError } from "../../components/form/ErrorSummary";
import AwardedLines from "../../components/purchasing/create/AwardedLines";
import type { AwardedLine, AwardedLines as Awarded } from "../../components/purchasing/api";
import { failure, label, mutationHeaders, type Problem } from "../../components/requisitions/api";
import { useLoad } from "../../components/requisitions/useLoad";
import { formatQuantity, parseQuantity, scaled } from "../../lib/decimal";
import "../../styles/projects.css";
import "../../styles/requisitions.css";
import "../../styles/sourcing.css";
import "../../styles/purchase-orders.css";

const ACCESS = "You cannot create purchase orders here. Ask an administrator for the purchase order create permission.";
const IN_FLIGHT = "This purchase order is still being created. Wait a moment, then try again.";
const NOT_ELIGIBLE = "This vendor is not eligible. Leave the quantities blank, or ask someone who can approve RFQs.";
const overOpen = (open: string) => `${formatQuantity(open)} open on this line. Order at most that.`;
/** Blank or all zeros: not ordering this line now. An all-zero entry parses once its zeros are made ones. */
const skipped = (text: string) => text.trim() === "" || (parseQuantity(text) === null && parseQuantity(text.replaceAll("0", "1")) !== null);
const link = (navigate: (href: string) => void, href: string, text: string) => <a className="req-link-button" href={href} onClick={e => { e.preventDefault(); navigate(href); }}>{text}</a>;

type Created = { id: string; number: string; vendor: string; requisition: string };
type Submission = { fingerprint: string; key: string };
type Nav = { navigate: (href: string) => void };

function Shell({ children }: { children: React.ReactNode }) { return <section className="req-page" aria-label="Create purchase orders">{children}</section>; }

export default function NewPurchaseOrders({ navigate }: Nav) {
  const award = new URLSearchParams(window.location.search).get("award") ?? "";
  const lines = useLoad(async () => {
    if (!award) return null;
    const { data, error, response } = await apiClient.GET("/api/v1/purchase-orders/awarded-lines", { params: { query: { award_id: award } } });
    if (data) return data;
    if (response?.status === 404) throw new Error("This award does not exist or you cannot see it.");
    if (response?.status === 403) throw new Error("You cannot order from this award. Ask an administrator for the purchase order view permission.");
    throw new Error(failure(error as Problem | undefined, "Loading awarded lines"));
  }, [award]);

  if (!award) return <Shell><section className="req-card" aria-label="Start from an award"><p>Purchase orders start from an approved award. Open the award and choose Create PO.</p>{link(navigate, "/sourcing/rfqs", "Go to RFQs")}</section></Shell>;
  if (lines.error) return <Shell><div role="alert"><p>{lines.error}</p><button type="button" onClick={lines.reload}>Try again</button></div></Shell>;
  const data = lines.data;
  if (!data) return <Shell><div className="req-card req-card-skeleton" aria-busy="true"><p className="req-skeleton" role="status" aria-label="Loading awarded lines" /></div></Shell>;
  if (data.award_status !== "approved") return <Shell><section className="req-card"><p>Award {data.award_number} is {label(data.award_status).toLowerCase()}. Only an approved award can be ordered from.</p>{link(navigate, `/sourcing/rfqs/${data.rfq_id}/award`, "Open the Award")}</section></Shell>;
  return <Form data={data} reload={lines.set} navigate={navigate} />;
}

function Form({ data, reload, navigate }: { data: Awarded; reload: (d: Awarded) => void } & Nav) {
  const [values, setValues] = useState<Record<string, string>>(() => Object.fromEntries(data.lines.filter(l => scaled(l.open_qty) > 0n).map(l => [l.rfq_line_id, formatQuantity(l.open_qty)])));
  const [reasons, setReasons] = useState<Record<string, string>>({});
  const [lineErrors, setLineErrors] = useState<Record<string, string>>({});
  const [vendorErrors, setVendorErrors] = useState<Record<string, string>>({});
  const [reasonErrors, setReasonErrors] = useState<Record<string, string>>({});
  const [groupErrors, setGroupErrors] = useState<Record<string, string>>({});
  const [problems, setProblems] = useState<SummaryError[]>([]);
  const [created, setCreated] = useState<Created[]>([]);
  const [runs, setRuns] = useState(0);
  const [busy, setBusy] = useState(false);
  const inFlight = useRef(false);
  const submissions = useRef<Record<string, Submission>>({});
  const summary = useRef<HTMLDivElement>(null);
  const done = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!runs) return;
    const target = problems.length ? summary.current?.querySelector<HTMLElement>("[role=alert]") : done.current?.querySelector<HTMLElement>("[role=status]");
    if (target) { target.tabIndex = -1; target.focus(); }
  }, [runs, problems]);

  const open = data.lines.filter(l => scaled(l.open_qty) > 0n);
  const vendors = [...data.vendors].sort((a, b) => a.vendor_name.localeCompare(b.vendor_name));
  const byVendor = (id: string) => data.lines.filter(l => l.vendor_id === id);
  const groupsOf = (lines: readonly AwardedLine[]) => {
    const groups = new Map<string, AwardedLine[]>();
    for (const l of lines) groups.set(l.requisition_id, [...(groups.get(l.requisition_id) ?? []), l]);
    return [...groups].sort((a, b) => a[1][0]!.requisition_number.localeCompare(b[1][0]!.requisition_number));
  };

  const run = async () => {
    if (inFlight.current) return;
    const errs: SummaryError[] = [], lineErr: Record<string, string> = {}, vendorErr: Record<string, string> = {}, reasonErr: Record<string, string> = {};
    const bodies: { gid: string; vendor: string; requisition: string; lines: AwardedLine[]; body: Record<string, unknown> }[] = [];
    for (const v of vendors) {
      for (const [requisitionId, rows] of groupsOf(byVendor(v.vendor_id))) {
        const lineBody: { rfq_line_id: string; quantity: string }[] = [], ordering: AwardedLine[] = [];
        for (const l of rows) {
          const text = values[l.rfq_line_id] ?? "";
          if (scaled(l.open_qty) === 0n || skipped(text)) continue;
          const quantity = parseQuantity(text), name = `Line ${l.line_no} of ${l.requisition_number}`;
          if (quantity === null) { lineErr[l.rfq_line_id] = `Enter a quantity like ${formatQuantity("1234.5")}.`; errs.push({ fieldId: `qty-${l.rfq_line_id}`, label: name, message: lineErr[l.rfq_line_id]! }); }
          else if (scaled(quantity) > scaled(l.open_qty)) { lineErr[l.rfq_line_id] = overOpen(l.open_qty); errs.push({ fieldId: `qty-${l.rfq_line_id}`, label: name, message: lineErr[l.rfq_line_id]! }); }
          else { lineBody.push({ rfq_line_id: l.rfq_line_id, quantity }); ordering.push(l); }
        }
        if (!lineBody.length) continue;
        const reason = (reasons[v.vendor_id] ?? "").trim();
        if (!v.eligible && !data.can_override && !vendorErr[v.vendor_id]) { vendorErr[v.vendor_id] = NOT_ELIGIBLE; errs.push({ fieldId: `vendor-${v.vendor_id}`, label: v.vendor_name, message: NOT_ELIGIBLE }); }
        if (!v.eligible && data.can_override && !reason && !reasonErr[v.vendor_id]) { reasonErr[v.vendor_id] = "Give a reason for ordering from a vendor who is not eligible."; errs.push({ fieldId: `override-${v.vendor_id}`, label: "Override Reason", message: reasonErr[v.vendor_id]! }); }
        bodies.push({ gid: `${v.vendor_id}:${requisitionId}`, vendor: v.vendor_name, requisition: rows[0]!.requisition_number, lines: ordering, body: { award_id: data.award_id, vendor_id: v.vendor_id, lines: lineBody, ...(v.eligible ? {} : { override_reason: reason }) } });
      }
    }
    if (!bodies.length && !errs.length) errs.push({ fieldId: `qty-${open[0]!.rfq_line_id}`, label: "Quantities", message: "Enter a quantity on at least one line." });
    setLineErrors(lineErr); setVendorErrors(vendorErr); setReasonErrors(reasonErr); setGroupErrors({}); setCreated([]);
    if (errs.length) { setProblems(errs); setRuns(n => n + 1); return; }
    inFlight.current = true; setBusy(true); setProblems([]);
    const made: Created[] = [], failed: SummaryError[] = [], gErr: Record<string, string> = {}, lErr: Record<string, string> = {}, vErr: Record<string, string> = {}, cleared: string[] = [];
    try {
      for (const g of bodies) {
        const fingerprint = JSON.stringify(g.body);
        if (submissions.current[g.gid]?.fingerprint !== fingerprint) submissions.current[g.gid] = { fingerprint, key: crypto.randomUUID() };
        const fail = (message: string) => { gErr[g.gid] = message; failed.push({ fieldId: `group-${g.gid}`, label: `${g.vendor}, ${g.requisition}`, message }); };
        try {
          const { data: po, error, response } = await apiClient.POST("/api/v1/purchase-orders", { body: g.body as never, headers: mutationHeaders(submissions.current[g.gid]!.key) });
          if (po) { delete submissions.current[g.gid]; made.push({ id: po.id, number: po.number, vendor: g.vendor, requisition: g.requisition }); cleared.push(...g.lines.map(l => l.rfq_line_id)); continue; }
          const e = error as Problem | undefined, problem = e?.checks?.problem;
          if (response.status === 403) fail(ACCESS);
          else if (response.status === 409 && e?.type?.endsWith("/request_in_flight")) fail(IN_FLIGHT);
          else if (response.status === 409 && problem === "over_open_quantity") {
            let shown = 0;
            for (const l of g.lines) {
              const figure = e?.checks?.[`open.${l.rfq_line_id}`] ?? e?.checks?.[`open.${l.requisition_line_id}`];
              if (figure === undefined) continue;
              shown++; lErr[l.rfq_line_id] = overOpen(figure);
              failed.push({ fieldId: `qty-${l.rfq_line_id}`, label: `Line ${l.line_no} of ${g.requisition}`, message: lErr[l.rfq_line_id]! });
            }
            if (!shown) fail(failure(e, "Creating the purchase order"));
          } else if (response.status === 409 && problem === "vendor_not_eligible") {
            const message = e?.checks?.reasons ? `Not eligible: ${e.checks.reasons}` : failure(e, "Creating the purchase order");
            const vendorId = String(g.body.vendor_id);
            vErr[vendorId] = message; failed.push({ fieldId: `vendor-${vendorId}`, label: g.vendor, message });
          } else fail(failure(e, "Creating the purchase order"));
        } catch { fail(failure(undefined, "Creating the purchase order")); } // the key is kept: a retry replays the same PO
      }
      const fresh = await apiClient.GET("/api/v1/purchase-orders/awarded-lines", { params: { query: { award_id: data.award_id } } }).catch(() => null);
      if (fresh?.data) reload(fresh.data);
      else failed.push({ fieldId: `qty-${open[0]!.rfq_line_id}`, label: "Figures", message: "The open quantities could not be refreshed. Reload the page to see them." });
      if (cleared.length) setValues(v => ({ ...v, ...Object.fromEntries(cleared.map(id => [id, ""])) }));
      setCreated(made); setGroupErrors(gErr); setLineErrors(lErr); setVendorErrors(vErr); setProblems(failed); setRuns(n => n + 1);
    } finally { inFlight.current = false; setBusy(false); }
  };

  return <Shell>
    <header className="req-heading"><h2><a href={`/sourcing/rfqs/${data.rfq_id}/award`} onClick={e => { e.preventDefault(); navigate(`/sourcing/rfqs/${data.rfq_id}/award`); }}>Award {data.award_number}</a></h2>{open.length > 0 && <p>Order the open quantities, one draft per vendor and requisition. Leave a quantity blank to order it later.</p>}</header>
    <div ref={summary}><ErrorSummary errors={problems} /></div>
    <div ref={done}>{created.length > 0 && <div role="status" className="req-card" aria-label="Created purchase orders"><ul>{created.map(c => <li key={c.id}>Created <a className="numeric" href={`/purchase-orders/${c.id}`} onClick={e => { e.preventDefault(); navigate(`/purchase-orders/${c.id}`); }}>{c.number}</a> for {c.vendor}, {c.requisition}.</li>)}</ul></div>}</div>
    {!open.length && <section className="req-card"><p>Everything on award {data.award_number} is already ordered.</p>{link(navigate, "/purchase-orders", "Go to Purchase Orders")}</section>}
    {open.length > 0 && <form className="req-card" noValidate aria-busy={busy || undefined} onSubmit={e => { e.preventDefault(); void run(); }}>
      {vendors.map(v => <AwardedLines key={v.vendor_id} vendor={v} lines={byVendor(v.vendor_id)} canOverride={data.can_override === true} values={values} errors={lineErrors} groupErrors={groupErrors} vendorError={vendorErrors[v.vendor_id]} reason={reasons[v.vendor_id] ?? ""} reasonError={reasonErrors[v.vendor_id]} onQuantity={(id, text) => setValues(s => ({ ...s, [id]: text }))} onReason={text => setReasons(s => ({ ...s, [v.vendor_id]: text }))} />)}
      <div className="req-actions"><button type="submit" className="form-submit" aria-busy={busy || undefined}><Plus aria-hidden="true" weight="regular" />{busy ? "Creating Draft POs" : "Create Draft POs"}</button></div>
    </form>}
  </Shell>;
}
