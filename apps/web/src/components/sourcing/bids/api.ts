import type { components } from "../../../api/generated/schema";
import { separators } from "../../../lib/decimal";
import { instantIn } from "../api";

export type Bid = components["schemas"]["BidRead"];
export type SealedBid = components["schemas"]["SealedBidRead"];
export type AnyBid = Bid | SealedBid;

/** The full shape carries lines; a sealed or someone else's draft read does not. */
export const isFull = (bid: AnyBid): bid is Bid => "lines" in bid;

/** UX-007: a typed amount in the viewer's locale to the canonical string the API takes (up to 4 decimals), or null. Zero is a real amount. */
export function parseAmount(input: string, locale?: string): string | null {
  const { group, decimal } = separators(locale);
  let text = input.trim().replace(/\s/gu, "");
  if (group) text = text.split(group.replace(/\s/gu, "")).join("");
  const canonical = text.replace(decimal, ".");
  return /^[0-9]{1,14}(?:\.[0-9]{1,4})?$/.test(canonical) ? canonical : null;
}

/** `Vendor Sent It On` in the RFQ's deadline zone to an ISO instant with offset, or the field error. */
export function sentInstant(date: string, time: string, zone: string): { iso: string } | { error: string } {
  const instant = instantIn(date, time, zone);
  if (instant) return { iso: instant.toISOString() };
  const real = instantIn(date, "12:00", zone), clock = /^([01]\d|2[0-3]):([0-5]\d)$/.test(time);
  return real && clock ? { error: `This time does not exist in ${zone} (the clocks change). Enter the vendor's time again.` } : { error: "Enter a real date and a time as HH:MM." };
}

/** Lateness is the vendor's sent time against the deadline the vendor was given, never the keyed time (decision 145). A display aid; the server decides. */
export const isLate = (bid: { sent_at: string }, deadlineAt: string) => new Date(bid.sent_at).getTime() > new Date(deadlineAt).getTime();

/** What the Invitees card says about a vendor's current bid; the words carry the meaning, not a colour (A11Y-004). */
export function bidState(bid: AnyBid | undefined, deadlineAt: string, now: number): string {
  if (!bid) return "No bid yet";
  if (bid.late) return "Late, Accepted By Override";
  if (bid.status === "draft") return isLate(bid, deadlineAt) ? "Draft, Late — Waiting for a Lead" : bid.replaces_bid_id ? "Replacement draft" : "Draft";
  return new Date(deadlineAt).getTime() > now ? "Received (sealed)" : "Received";
}

/** The bid a vendor's row opens: its live draft first, else its submitted bid that nothing replaced. */
export function currentBid(bids: AnyBid[], vendorId: string): AnyBid | undefined {
  const mine = bids.filter(b => b.vendor_id === vendorId);
  return mine.find(b => b.status === "draft") ?? mine.find(b => b.status === "submitted" && !b.replaced_by_bid_id);
}
