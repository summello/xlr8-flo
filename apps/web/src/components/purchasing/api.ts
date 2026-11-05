import type { components } from "../../api/generated/schema";
import type { StatusTone } from "../status/tones";
import { formatDate } from "../requisitions/api";

export type Po = components["schemas"]["PoRead"];
export type PoRow = components["schemas"]["PoListRow"];
export type PoCounts = components["schemas"]["PoPage"]["counts"];
export type PoVersion = components["schemas"]["PoVersionRead"];
export type PoHistory = components["schemas"]["PoHistoryRead"];
export type PoBudget = components["schemas"]["PoBudgetSummaryRead"];
export type PoTransmission = components["schemas"]["PoTransmissionRead"];
export type PoChange = components["schemas"]["PoChangeRead"];
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
