import { Envelope, FilePdf } from "@phosphor-icons/react";
import { useEffect, useRef, useState } from "react";
import { apiClient } from "../../api/client";
import { writeHeaders } from "../../features/auth/api";
import StatusPill from "../../components/status/StatusPill";
import { BudgetCard, HistoryCard, LinesCard, LinkedCard, NotesAndFiles, PoStepper, Swimlanes } from "../../components/purchasing/PoCards";
import IssueDialog from "../../components/purchasing/issue/IssueDialog";
import CancelOrReduce from "../../components/purchasing/dialogs/CancelOrReduce";
import VendorCopySheet from "../../components/purchasing/vendor-copy/VendorCopySheet";
import ChangeRecipient from "../../components/purchasing/transmissions/ChangeRecipient";
import { CANCEL_EMAIL, currentSend, ISSUE_EMAIL, recovery } from "../../components/purchasing/transmissions/recovery";
import TransmissionsCard from "../../components/purchasing/transmissions/TransmissionsCard";
import { useResend } from "../../components/purchasing/transmissions/useResend";
import VersionControl from "../../components/purchasing/VersionControl";
import { failure, type Problem } from "../../components/requisitions/api";
import { useLoad } from "../../components/requisitions/useLoad";
import "../../styles/projects.css";
import "../../styles/requisitions.css";
import "../../styles/vendors.css";
import "../../styles/sourcing.css";
import "../../styles/purchase-orders.css";

const HAS_PDF = ["issued", "superseded", "closed"];

export default function PurchaseOrderDetail({ id, navigate }: { id: string; navigate: (href: string) => void }) {
  const po = useLoad(async () => {
    const { data, error, response } = await apiClient.GET("/api/v1/purchase-orders/{id}", { params: { path: { id } } });
    if (!data) throw new Error(response?.status === 404 ? "This purchase order does not exist or you cannot see it." : failure(error as Problem | undefined, "Loading the purchase order"));
    return data;
  }, [id]);
  useEffect(() => { if (po.data) document.title = `PO ${po.data.number}`; }, [po.data]);

  // The sends belong to the page, so a resend from the header updates the card too.
  const sends = useLoad(async () => {
    const { data, error } = await apiClient.GET("/api/v1/purchase-orders/{id}/transmissions", { params: { path: { id } } });
    if (!data) throw new Error(failure(error as Problem | undefined, "Loading transmissions"));
    return data;
  }, [id]);
  const { resend, busy: resending, refusal, clearRefusal } = useResend(id, rows => sends.set(rows));
  const [sheetOpener, setSheetOpener] = useState<HTMLElement | null>(null);
  const [sheetOpen, setSheetOpen] = useState(false);
  const [startError, setStartError] = useState<string | null>(null);
  const starting = useRef(false);
  const startChange = async () => {
    if (starting.current) return;
    starting.current = true; setStartError(null);
    try {
      const path = { id };
      const { data, error } = await apiClient.POST("/api/v1/purchase-orders/{id}/changes", { params: { path }, headers: writeHeaders(), body: {} });
      if (data) return navigate(`/purchase-orders/${id}/changes/${data.number}`);
      const problem = error as Problem | undefined;
      if (problem?.checks?.problem === "change_in_progress") {
        const open = (await apiClient.GET("/api/v1/purchase-orders/{id}/changes", { params: { path } })).data?.find(c => c.status === "draft" || c.status === "approval_pending");
        if (open) return navigate(`/purchase-orders/${id}/changes/${open.number}`);
      }
      setStartError(failure(problem, "Starting a change order"));
    } catch { setStartError(failure(undefined, "Starting a change order")); }
    finally { starting.current = false; }
  };

  if (po.error) return <section className="req-page" aria-label="Purchase order"><div role="alert"><p>{po.error}</p><button type="button" onClick={po.reload}>Try again</button></div><a href="/purchase-orders" onClick={e => { e.preventDefault(); navigate("/purchase-orders"); }}>Back to purchase orders</a></section>;
  if (!po.data) return <section className="req-page" aria-label="Purchase order"><div className="req-card req-card-skeleton" aria-busy="true"><p className="req-skeleton" role="status" aria-label="Loading purchase order" /></div></section>;
  const p = po.data;
  const template = p.status === "issued" ? ISSUE_EMAIL : p.status === "cancelled" ? CANCEL_EMAIL : null;
  const plan = template && sends.data ? recovery(currentSend(sends.data, p.id, template), p.vendor_status !== null) : null;
  return <section className="req-page vendor-profile po-page" aria-label="Purchase order detail">
    <header className="vendor-head">
      <div><h2 className="vendor-name po-number">{p.number}</h2>
        <p className="req-sub"><a href={`/vendors/${p.vendor_id}`}>{typeof p.vendor_snapshot.trading_name === "string" ? p.vendor_snapshot.trading_name : "Vendor"}</a>{p.vendor_status && <> · Vendor {p.vendor_status.replaceAll("_", " ")}</>} · <a href={`/requisitions/${p.requisition_id}`}>Requisition</a></p></div>
      <VersionControl key={p.id} po={p} navigate={navigate} />
      <StatusPill docType="purchase_order" status={p.status as "draft"} />
      <div className="vendor-head-actions">{p.actions?.includes("issue") && <IssueDialog po={p} navigate={navigate} onReload={po.reload} />}<CancelOrReduce po={p} onDone={po.reload} />{p.actions?.includes("change_order") && <button type="button" className="req-link-button" onClick={() => void startChange()}>Start Change Order</button>}{(p.actions ?? []).includes("edit") && <a className="req-link-button" href={`/purchase-orders/${p.id}/edit`} onClick={e => { e.preventDefault(); navigate(`/purchase-orders/${p.id}/edit`); }}>Edit</a>}{HAS_PDF.includes(p.status) && <a className="req-link-button" href={`/api/v1/purchase-orders/${p.id}/pdf`} target="_blank" rel="noreferrer"><FilePdf aria-hidden="true" weight="regular" />View PDF</a>}<button type="button" className="req-link-button" onClick={e => { setSheetOpener(e.currentTarget); setSheetOpen(true); }}><Envelope aria-hidden="true" weight="regular" />Vendor Copy</button>{plan?.actions.map(a => a === "resend"
        ? <button key={a} type="button" onClick={() => void resend()} aria-busy={resending || undefined} disabled={resending}>{resending ? "Resending…" : "Resend"}</button>
        : <ChangeRecipient key={a} poId={p.id} vendorId={p.vendor_id} onSent={rows => { sends.set(rows); clearRefusal(); }} />)}
        {plan?.actions.includes("resend") && <span className="req-sub po-resend-note">Sends the same version again. Nothing more is committed.</span>}
        {refusal && <p role="alert" className="field-error">{refusal}</p>}</div>
    </header>
    {sheetOpen && <VendorCopySheet id={id} opener={sheetOpener} navigate={navigate} onClose={() => setSheetOpen(false)} />}
    {startError && <p role="alert">{startError}</p>}
    <div className="po-bar"><PoStepper po={p} /><Swimlanes po={p} /></div>
    <div className="po-body">
      <div className="po-col"><LinesCard po={p} /><NotesAndFiles id={id} /><TransmissionsCard id={id} status={p.status} load={sends} /></div>
      <div className="po-col"><BudgetCard key={`budget-${p.version}`} id={id} navigate={navigate} /><LinkedCard po={p} /><HistoryCard key={`history-${p.version}`} id={id} /></div>
    </div>
  </section>;
}
