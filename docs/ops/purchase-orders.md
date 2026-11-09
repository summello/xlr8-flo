# Purchase orders — operator notes

## Vendor-facing mark on attachments (E13-S25)

Each file on a purchase order can be marked **vendor-facing**. The mark records intent only.
**Nothing is sent to the supplier until E19-S04 (attachment scanning) ships:** the supplier email
carries one PDF and the PDF names no file, whatever the mark says.

- Default is internal. Marking needs `purchase_order.create` while the PO is a draft and
  `purchase_order.amend` in every other status. A cancelled PO refuses it (409).
- The mark belongs to the file, so it follows the file across change orders.
- **Uploading a new version of a marked file resets the mark.** The new bytes read as internal
  until someone marks them again.
- A mark can be set on a file that has not passed scanning; the UI says "Not sent until scanning
  available".
- Marking does not change the PO's version, so it never conflicts with an issue, approval or cancel
  in progress. Every change is audited as `purchase_order.attachment.vendor_facing`.

## Change orders (E13-S18)

An issued purchase order is a record. To change it, press **Start Change Order** on the PO (needs
`purchase_order.amend`). The change order screen shows what changes, the signed budget effect and
whether approval is needed; every figure comes from the server.

- A change order may change a line's quantity (and its price, tax or freight, price only downward),
  the document freight, the terms and the needed-by date. Project, department and ledger account
  cannot change here: use a budget transfer.
- **A date or terms change applies at once** (`Apply`): it becomes the next version with no approval.
- **An amount change** (quantity, price, tax, freight, line count) **needs the `po_change`
  workflow**: `Submit for Approval`. With no published workflow the change order stays a draft and
  the screen says so.
- **A lower amount is held again for the requisition**: the difference is released from the PO's
  commitment, so another PO can order it without new approval.
- The prior version stays readable from the version control. Withdrawing a submitted change order,
  discarding a draft, and removing or adding lines are not on this screen yet.

## Editing a draft (E13-S14)

A draft can be changed at `/purchase-orders/<id>/edit` by a user who holds `purchase_order.create` on its unit. It may change Ship To (any active Ship To of the unit or above), terms, needed-by date, line quantities, line tax and freight, and the freight mode (per line or per document). Totals are recomputed by the server on every save; the screen never sums them. **Submit for Approval saves unsaved edits first**, then submits the saved draft; if the save fails nothing is submitted. Only the buyer who drafted the order can submit it. The vendor, its recipients and any eligibility override are fixed when the draft is created and cannot change here. Notes are internal unless marked Sent to Vendor; a file marked For the vendor is not sent until scanning is available.

The keyboard-only walkthrough from a project to an issued order, with its dev seed, is `docs/ops/acc-008-walkthrough.md` (E13-S29).
