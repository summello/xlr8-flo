import VendorCopyView from "../../components/purchasing/vendor-copy/VendorCopyView";
import { useVendorCopy } from "../../components/purchasing/vendor-copy/useVendorCopy";
import "../../styles/projects.css";
import "../../styles/requisitions.css";
import "../../styles/vendors.css";
import "../../styles/sourcing.css";
import "../../styles/purchase-orders.css";

export default function PurchaseOrderVendorCopy({ id, navigate }: { id: string; navigate: (href: string) => void }) {
  const copy = useVendorCopy(id);
  if (copy.error) return <section className="req-page" aria-label="Vendor copy"><div role="alert"><p>{copy.error}</p><button type="button" onClick={copy.reload}>Try again</button></div><a href="/purchase-orders" onClick={e => { e.preventDefault(); navigate("/purchase-orders"); }}>Back to purchase orders</a></section>;
  if (!copy.data) return <section className="req-page" aria-label="Vendor copy"><div className="req-card req-card-skeleton" aria-busy="true"><p className="req-skeleton" role="status" aria-label="Loading vendor copy" /></div></section>;
  return <section className="req-page po-page" aria-label="Vendor copy"><h2 className="visually-hidden">Vendor Copy</h2><VendorCopyView copy={copy.data} poId={id} /></section>;
}
