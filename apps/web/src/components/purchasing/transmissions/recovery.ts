import type { PoTransmission } from "../api";

export type Recovery = {
  /** Primary first. */
  actions: ("change_recipient" | "resend")[];
  /** What takes focus; "message" is the failure text itself (nothing to act on). */
  focus: "change_recipient" | "resend" | "message";
};

/** The result values that need recovery (MASTER 7.16.3, DECISIONS 174, operator decision 3 for `complained`). */
export const FAILED: readonly PoTransmission["result"][] = ["failed", "bounced", "address_suppressed", "complained"];
export const TERMINAL: readonly PoTransmission["result"][] = [...FAILED, "delivered"];

/** Null when nothing in the send failed. `canChangeRecipient` is "the viewer holds vendor.view" (operator decision 2). */
export function recovery(rows: readonly Pick<PoTransmission, "result">[], canChangeRecipient: boolean): Recovery | null {
  const has = (r: PoTransmission["result"]) => rows.some(x => x.result === r);
  if (!rows.some(x => FAILED.includes(x.result))) return null;
  if (has("complained")) return canChangeRecipient ? { actions: ["change_recipient"], focus: "change_recipient" } : { actions: [], focus: "message" };
  if (has("bounced") || has("address_suppressed")) return canChangeRecipient ? { actions: ["change_recipient", "resend"], focus: "change_recipient" } : { actions: ["resend"], focus: "resend" };
  return { actions: canChangeRecipient ? ["resend", "change_recipient"] : ["resend"], focus: "resend" };
}
