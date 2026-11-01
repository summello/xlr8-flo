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
