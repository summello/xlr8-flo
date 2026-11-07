import type { components } from "../../api/generated/schema";
import type { StatusTone } from "../status/tones";
import { formatMoney } from "../../lib/money";
import { formatDate } from "../requisitions/api";

export type Po = components["schemas"]["PoRead"];
export type PoRow = components["schemas"]["PoListRow"];
export type PoCounts = components["schemas"]["PoPage"]["counts"];
export type PoVersion = components["schemas"]["PoVersionRead"];
export type PoHistory = components["schemas"]["PoHistoryRead"];
export type PoBudget = components["schemas"]["PoBudgetSummaryRead"];
export type PoTransmission = components["schemas"]["PoTransmissionRead"];
export type VendorCopyRead = components["schemas"]["VendorCopyRead"];
export type PoChange = components["schemas"]["PoChangeRead"];
export type PoCancelPreview = components["schemas"]["PoCancelPreviewRead"];
export type AwardedLines = components["schemas"]["AwardedLinesRead"];
export type AwardedLine = components["schemas"]["AwardedLineRead"];
export type AwardedVendor = components["schemas"]["AwardedVendorRead"];
type Sent = NonNullable<PoRow["sent"]>;

/** PO list tabs (pages/purchase-orders.md 4.1); the counts come from the server, one key each. */
export const TABS = [["draft", "Drafts"], ["awaiting", "Awaiting Approval"], ["ready", "Ready to Issue"], ["issued", "Issued"], ["attention", "Needs Attention"], ["all", "All"]] as const;
export type Tab = (typeof TABS)[number][0];

/** The stepper; the M4 states are drawn as future (goods receipt arrives with M4). Cancelled and Superseded are banners. */
export const LIFECYCLE = [
  ["draft", "Draft"], ["approval_pending", "Approval Pending"], ["approved", "Approved"], ["issued", "Issued"],
  ["partially_delivered", "Partially Delivered"], ["delivered", "Delivered"], ["accepted", "Accepted"], ["closed", "Closed"],
] as const;
export const FUTURE_STATES: readonly string[] = ["partially_delivered", "delivered", "accepted"];

const SENT: Record<Sent["result"], [StatusTone, string]> = {
  queued: ["info", "Queued"], sent: ["neutral", "Sent"], delivered: ["success", "Delivered"],
  failed: ["danger", "Failed"], bounced: ["danger", "Bounced"], complained: ["danger", "Complained"],
  address_suppressed: ["warning", "Address suppressed"],
};

/** The words of a transmission result with its date (`Delivered 12 Oct`); colour and icon only repeat them. */
export function sentSignal(sent: Pick<Sent, "result" | "result_at">): { tone: StatusTone; label: string } {
  const [tone, word] = SENT[sent.result];
  return { tone, label: sent.result_at ? `${word} ${formatDate(sent.result_at)}` : word };
}

/** What the PO holds in the budget now, in a sentence (DECISIONS 210). */
export const HOLDS: Record<"reserved" | "committed" | "released", string> = {
  reserved: "This PO holds a reservation: nothing is committed until it is issued.",
  committed: "This PO holds a commitment.",
  released: "This PO released its commitment.",
};

const ref = (p: { code: string; name: string }) => `${p.code} ${p.name}`;

/** The money sentence of the cancel dialog (pages/purchase-orders.md 2), from the server's preview; the browser adds nothing up. */
export function cancelConsequence(p: PoCancelPreview, alsoCancelLines: boolean): string {
  if (p.status !== "issued") {
    return `Nothing was issued, so nothing is committed or released. The requisition keeps its reservation.${p.status === "approval_pending" ? " The approval request is withdrawn." : ""}`;
  }
  if (!p.released_currency || /^0(?:\.0*)?$/.test(p.released)) return "Nothing is left to release: the commitment is used.";
  const amount = `${formatMoney(p.released, p.released_currency)} ${p.released_currency}`;
  if (p.rereserved && p.reservation_project) {
    return alsoCancelLines
      ? `Releases ${amount} once and returns it to ${ref(p.main_project)}. The requisition lines are cancelled.`
      : `Releases ${amount} once and re-reserves it for requisition ${p.requisition_number} on ${ref(p.reservation_project)}, so another PO can order the reopened quantity without new approval.`;
  }
  return `Releases ${amount} once. The requisition holds no reservation, so the money is available again on the project it was committed to.${alsoCancelLines ? " The requisition lines are cancelled." : ""}`;
}
