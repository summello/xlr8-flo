import * as Dialog from "@radix-ui/react-dialog";
import { X } from "@phosphor-icons/react";
import { restoreFocus } from "../../../lib/focus";
import "../../../styles/approvals.css";
import VendorCopyView from "./VendorCopyView";
import { useVendorCopy } from "./useVendorCopy";

type Props = { id: string; opener: HTMLElement | null; navigate: (href: string) => void; onClose: () => void };

/** The vendor copy beside the PO detail; mounted only while open, so it loads when opened. */
export default function VendorCopySheet({ id, opener, navigate, onClose }: Props) {
  const copy = useVendorCopy(id);
  const full = `/purchase-orders/${id}/vendor-copy`;
  return <Dialog.Root open onOpenChange={open => { if (!open) onClose(); }}>
    <Dialog.Portal>
      <Dialog.Overlay className="appr-scrim" />
      <Dialog.Content className="appr-sheet vendor-sheet" aria-describedby="vc-sheet-desc" onCloseAutoFocus={event => { event.preventDefault(); restoreFocus(opener); }}>
        <header className="appr-sheet-head">
          <div><Dialog.Title>Vendor Copy</Dialog.Title>
            <Dialog.Description id="vc-sheet-desc" className="visually-hidden">What the supplier receives: one email and one PDF.</Dialog.Description></div>
          <Dialog.Close className="appr-close" aria-label="Close"><X aria-hidden="true" />Close</Dialog.Close>
        </header>
        <div><a href={full} onClick={e => { e.preventDefault(); onClose(); navigate(full); }}>Open full page</a></div>
        {copy.error ? <div role="alert"><p>{copy.error}</p><button type="button" onClick={copy.reload}>Try again</button></div>
          : copy.data ? <VendorCopyView copy={copy.data} poId={id} />
          : <div className="req-card req-card-skeleton" aria-busy="true"><p className="req-skeleton" role="status" aria-label="Loading vendor copy" /></div>}
      </Dialog.Content>
    </Dialog.Portal>
  </Dialog.Root>;
}
