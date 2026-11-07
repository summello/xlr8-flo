import { FilePdf } from "@phosphor-icons/react";
import { useEffect } from "react";
import { apiClient } from "../../api/client";
import StatusPill from "../../components/status/StatusPill";
import { BudgetCard, HistoryCard, LinesCard, LinkedCard, NotesAndFiles, PoStepper, Swimlanes, TransmissionsCard } from "../../components/purchasing/PoCards";
import CancelOrReduce from "../../components/purchasing/dialogs/CancelOrReduce";
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

  if (po.error) return <section className="req-page" aria-label="Purchase order"><div role="alert"><p>{po.error}</p><button type="button" onClick={po.reload}>Try again</button></div><a href="/purchase-orders" onClick={e => { e.preventDefault(); navigate("/purchase-orders"); }}>Back to purchase orders</a></section>;
  if (!po.data) return <section className="req-page" aria-label="Purchase order"><div className="req-card req-card-skeleton" aria-busy="true"><p className="req-skeleton" role="status" aria-label="Loading purchase order" /></div></section>;
  const p = po.data;
  return <section className="req-page vendor-profile po-page" aria-label="Purchase order detail">
    <header className="vendor-head">
      <div><h2 className="vendor-name po-number">{p.number}</h2>
        <p className="req-sub"><a href={`/vendors/${p.vendor_id}`}>{typeof p.vendor_snapshot.trading_name === "string" ? p.vendor_snapshot.trading_name : "Vendor"}</a>{p.vendor_status && <> · Vendor {p.vendor_status.replaceAll("_", " ")}</>} · <a href={`/requisitions/${p.requisition_id}`}>Requisition</a></p></div>
      <VersionControl key={p.id} po={p} navigate={navigate} />
      <StatusPill docType="purchase_order" status={p.status as "draft"} />
      <div className="vendor-head-actions"><CancelOrReduce po={p} onDone={po.reload} />{HAS_PDF.includes(p.status) && <a className="req-link-button" href={`/api/v1/purchase-orders/${p.id}/pdf`} target="_blank" rel="noreferrer"><FilePdf aria-hidden="true" weight="regular" />View PDF</a>}</div>
    </header>
    <div className="po-bar"><PoStepper po={p} /><Swimlanes po={p} /></div>
    <div className="po-body">
      <div className="po-col"><LinesCard po={p} /><NotesAndFiles id={id} /><TransmissionsCard id={id} /></div>
      <div className="po-col"><BudgetCard key={`budget-${p.version}`} id={id} navigate={navigate} /><LinkedCard po={p} /><HistoryCard key={`history-${p.version}`} id={id} /></div>
    </div>
  </section>;
}
