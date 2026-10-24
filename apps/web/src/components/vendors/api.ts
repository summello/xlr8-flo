import type { components } from "../../api/generated/schema";
import type { StatusTone } from "../status/tones";

export type VendorRow = components["schemas"]["VendorRead"];
export type VendorDetail = components["schemas"]["VendorDetail"];
export type DocState = components["schemas"]["DocState"];
export type DocSummary = components["schemas"]["DocSummary"];
export type VendorCounts = components["schemas"]["VendorCounts"];
export type Contact = components["schemas"]["ContactRead"];
export type Change = components["schemas"]["ChangeRead"];
export type Unit = components["schemas"]["OrgUnitRead"];

/** Words and tone for one document standing. Words carry the meaning; the icon repeats it (A11Y-004). */
export function docWords(state: DocState["state"], daysLeft?: number | null, count?: number): { text: string; tone: StatusTone } {
  const n = count === undefined ? "" : `${count} `;
  switch (state) {
    case "expiring": return { text: daysLeft === 0 ? `${n}Expiring Today` : `${n}Expiring in ${daysLeft ?? 0} ${daysLeft === 1 ? "Day" : "Days"}`, tone: "warning" };
    case "expired": return { text: `${n}Expired`, tone: "danger" };
    case "missing": return { text: `${n}Missing`, tone: "danger" };
    case "rejected": return { text: `${n}Rejected`, tone: "danger" };
    case "unverified": return { text: `${n}Awaiting Verification`, tone: "info" };
    default: return { text: `${n}Verified`, tone: "success" };
  }
}

export const AVAILABILITY = { global: "Global", restricted: "Selected Units" } as const;
