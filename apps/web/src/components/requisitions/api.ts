import type { components } from "../../api/generated/schema";
import { writeHeaders } from "../../features/auth/api";

export type Requisition = components["schemas"]["RequisitionRead"];
export type RequisitionStatus = Requisition["status"];
export type RequisitionLine = components["schemas"]["LineRead"];
export type LineBody = components["schemas"]["ReplacementLine"];
export type SubmissionCheck = components["schemas"]["SubmissionCheck"];
export type LookupOption = components["schemas"]["LookupOption"];
export type CatalogueItem = components["schemas"]["CatalogueItemRead"];
export type Attachment = components["schemas"]["AttachmentRead"];
export type StatusEvent = components["schemas"]["StatusEventRead"];
export type Linked = components["schemas"]["LinkedRecord"];
export type Available = components["schemas"]["RequisitionTransitionAvailable"];
export type Decision = components["schemas"]["DecisionRead"];
export type Problem = components["schemas"]["ProblemDetails"];

export const statuses: readonly RequisitionStatus[] = [
  "draft", "approval_pending", "sourcing_in_progress", "sourced", "awarded", "open_for_purchase",
  "completed", "rejected", "withdrawn", "cancelled", "expired",
];

export function label(value: string) { return value.replaceAll("_", " ").replace(/^./, c => c.toUpperCase()); }

/** Headers for a state-changing call: CSRF, a key the caller keeps stable across retries, and the version. */
export function mutationHeaders(key: string, version?: number): Record<string, string> {
  return { ...writeHeaders(), "Idempotency-Key": key, ...(version === undefined ? {} : { "If-Match": String(version) }) };
}

/** UX-007: dates in the viewer's locale; the ISO value stays canonical in state and on the wire. */
export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const date = /^\d{4}-\d{2}-\d{2}$/.test(iso) ? new Date(`${iso}T00:00:00`) : new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : new Intl.DateTimeFormat(undefined, { dateStyle: "medium" }).format(date);
}
export function formatDateTime(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(date);
}
export function formatTime(date: Date): string {
  return new Intl.DateTimeFormat(undefined, { timeStyle: "short" }).format(date);
}

/** UX-005: what happened, what was kept, how to recover, plus the reference id. */
export function failure(error: { detail?: string | null; correlation_id?: string | null } | undefined, action: string): string {
  const ref = error?.correlation_id ? ` Reference ${error.correlation_id}.` : "";
  return error?.detail
    ? `${error.detail} Your entries and loaded data are kept. Correct the issue or try again.${ref}`
    : `${action} failed. Your entries and loaded data are kept. Check your connection and try again.${ref}`;
}
