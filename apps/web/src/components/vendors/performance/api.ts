import type { components } from "../../../api/generated/schema";

export type Performance = components["schemas"]["PerformanceRead"];
export type Score = components["schemas"]["ScoreRead"];
export type Dispute = components["schemas"]["DisputeRead"];

/** The four core categories the server seeds; any other defined category shows up in the card and joins this list. */
export const CORE_CATEGORIES = ["QUALITY", "PRICING", "DELIVERY", "SERVICE"];

export const openDispute = (s: Score): Dispute | undefined => s.disputes.find(d => d.resolution === null);

/** A `<input type="month">` value ("2026-10") as the first of that month, the server's period. */
export const monthStart = (month: string): string => `${month}-01`;
