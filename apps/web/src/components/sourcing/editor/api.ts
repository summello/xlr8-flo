import type { components } from "../../../api/generated/schema";
import { apiClient } from "../../../api/client";
import { formatQuantity, parseQuantity, scaled } from "../../../lib/decimal";
import type { SummaryError } from "../../form/ErrorSummary";
import { failure, mutationHeaders, type Problem } from "../../requisitions/api";
import { instantIn, wallClock, type Rfq } from "../api";

export type Candidate = components["schemas"]["InviteeCandidate"];
export type Mode = "edit" | "amend";

export const TEXT = [
  ["specifications", "Specifications"],
  ["delivery_location", "Delivery Location"],
  ["delivery_terms", "Delivery Terms"],
  ["terms", "Terms"],
] as const;
export type TextKey = (typeof TEXT)[number][0];

/** What the cards edit. Everything else the editor does goes straight to the server and comes back as an `Rfq`. */
export type Form = Record<TextKey, string> & { date: string; time: string; lineSpecs: Record<string, string>; qty: Record<string, string> };
export type Change = { key: string; label: string; was: string; now: string };

export function formOf(rfq: Rfq): Form {
  const clock = wallClock(rfq.deadline_at, rfq.deadline_tz ?? "UTC");
  return {
    specifications: rfq.specifications ?? "", delivery_location: rfq.delivery_location ?? "", delivery_terms: rfq.delivery_terms ?? "", terms: rfq.terms ?? "",
    date: clock.date, time: clock.time,
    lineSpecs: Object.fromEntries(rfq.lines.map(l => [l.id, l.specification ?? ""])),
    qty: Object.fromEntries(rfq.lines.map(l => [l.requisition_line_id, formatQuantity(l.quantity)])),
  };
}

const none = (text: string) => (text.trim() === "" ? "none" : text.trim());

/** `What Changed`: one row per edited field, computed in the browser because the server only diffs inside the amend transaction. */
export function changes(rfq: Rfq, form: Form): Change[] {
  const zone = rfq.deadline_tz ?? "UTC", out: Change[] = [];
  for (const [key, label] of TEXT) if ((rfq[key] ?? "").trim() !== form[key].trim()) out.push({ key, label, was: none(rfq[key] ?? ""), now: none(form[key]) });
  const instant = instantIn(form.date, form.time, zone);
  if (!instant || instant.getTime() !== new Date(rfq.deadline_at).getTime()) {
    const was = wallClock(rfq.deadline_at, zone);
    out.push({ key: "deadline", label: "Response Deadline", was: `${was.date} ${was.time} ${zone}`, now: `${form.date} ${form.time} ${zone}` });
  }
  for (const l of rfq.lines) {
    if ((l.specification ?? "").trim() !== (form.lineSpecs[l.id] ?? "").trim()) out.push({ key: `spec-${l.id}`, label: `Line ${l.line_no} specification`, was: none(l.specification ?? ""), now: none(form.lineSpecs[l.id] ?? "") });
    const quantity = parseQuantity(form.qty[l.requisition_line_id] ?? "");
    if (quantity === null || scaled(quantity) !== scaled(l.quantity)) out.push({ key: `qty-${l.id}`, label: `Line ${l.line_no} quantity`, was: formatQuantity(l.quantity), now: form.qty[l.requisition_line_id] ?? "" });
  }
  return out;
}

export type Built = { body: Record<string, unknown>; errors: SummaryError[]; quantityChanged: boolean };

/** The request body for a draft save (everything) or an amendment (what changed). Malformed entries come back as field errors; nothing is sent. */
export function build(rfq: Rfq, form: Form, mode: Mode): Built {
  const zone = rfq.deadline_tz ?? "UTC", errors: SummaryError[] = [], body: Record<string, unknown> = {};
  const edited = new Set(changes(rfq, form).map(c => c.key));
  for (const [key] of TEXT) if (mode === "edit" || edited.has(key)) body[key] = form[key].trim() === "" ? null : form[key];
  const instant = instantIn(form.date, form.time, zone);
  if (!instant) errors.push({ fieldId: "rfq-date", label: "Response Deadline", message: "Enter a date as YYYY-MM-DD and a 24-hour time as HH:MM." });
  else if (mode === "edit" || edited.has("deadline")) { body.deadline_at = instant.toISOString(); body.deadline_tz = zone; }
  const specs = Object.fromEntries(rfq.lines.filter(l => mode === "edit" ? (l.specification ?? "") !== form.lineSpecs[l.id] : edited.has(`spec-${l.id}`)).map(l => [l.id, form.lineSpecs[l.id]!.trim() === "" ? null : form.lineSpecs[l.id]!]));
  if (Object.keys(specs).length) body.line_specifications = specs;
  const quantityChanged = rfq.lines.some(l => edited.has(`qty-${l.id}`));
  if (mode === "amend" && quantityChanged) {
    const lines = rfq.lines.map(l => ({ requisition_line_id: l.requisition_line_id, requisition_version: l.requisition_version, quantity: parseQuantity(form.qty[l.requisition_line_id] ?? "") }));
    for (const [n, line] of lines.entries()) if (line.quantity === null) errors.push({ fieldId: `rfq-qty-${rfq.lines[n]!.requisition_line_id}`, label: `Line ${rfq.lines[n]!.line_no} quantity`, message: `Enter a quantity like ${formatQuantity("1234.5")}.` });
    body.lines = lines;
  }
  return { body, errors, quantityChanged };
}

const FIELD_IDS: Record<string, [string, string]> = {
  specifications: ["rfq-specifications", "Specifications"], delivery_location: ["rfq-delivery_location", "Delivery Location"], delivery_terms: ["rfq-delivery_terms", "Delivery Terms"], terms: ["rfq-terms", "Terms"],
  deadline_at: ["rfq-date", "Response Deadline"], reason: ["amend-reason", "Reason"], lines: ["rfq-lines", "Lines"], invitees: ["rfq-invitees", "Invited Vendors"],
};

/** The server's `errors[]` as summary rows, each by its message, whatever the field: a later problem needs no change here. */
export function summaryOf(problem: Problem | undefined, fallback: string): SummaryError[] {
  const rows = (problem?.errors ?? []).map(e => {
    const [fieldId, label] = FIELD_IDS[e.field] ?? (e.field.startsWith("invitees/") ? FIELD_IDS.invitees! : ["rfq-form", "RFQ"]);
    return { fieldId, label, message: e.message };
  });
  return rows.length > 0 ? rows : [{ fieldId: "rfq-form", label: "RFQ", message: failure(problem, fallback) }];
}

export async function fetchRfq(id: string): Promise<Rfq> {
  const { data, error, response } = await apiClient.GET("/api/v1/sourcing/rfqs/{id}", { params: { path: { id } } });
  if (!data) throw new Error(response?.status === 404 ? "This RFQ does not exist or you cannot see it." : failure(error, "Loading the RFQ"));
  return data;
}

/** One write: `Rfq` back, or the words for the page to show. Every write here sends the RFQ's revision; POSTs also carry their own key. */
export async function write(run: (headers: Record<string, string>) => Promise<{ data?: Rfq; error?: unknown }>, rfq: Rfq, action: string): Promise<{ rfq: Rfq; message?: undefined } | { rfq?: undefined; message: string; problem?: Problem }> {
  try {
    const { data, error } = await run({ ...mutationHeaders(crypto.randomUUID()), "If-Match": String(rfq.edit_revision) });
    return data ? { rfq: data } : { message: (error as Problem | undefined)?.errors?.[0]?.message ?? failure(error as Problem | undefined, action), problem: error as Problem | undefined };
  } catch {
    return { message: failure(undefined, action) };
  }
}
