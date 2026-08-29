import type { DocType, KnownStatus } from "./map";

export const STATUS_LABELS = {
  draft: "Draft",
  approval_pending: "Approval pending",
  active: "Active",
  deferred: "Deferred",
  completed: "Completed",
  abandoned: "Abandoned",
  sourcing_in_progress: "Sourcing in progress",
  sourced: "Sourced",
  awarded: "Awarded",
  open_for_purchase: "Open for purchase",
  cancelled: "Cancelled",
  approved: "Approved",
  issued: "Issued",
  partially_fulfilled: "Partially fulfilled",
  fulfilled: "Fulfilled",
  closed: "Closed",
  superseded: "Superseded",
  published: "Published",
  under_evaluation: "Under evaluation",
  pending: "Pending",
  suspended: "Suspended",
  expired: "Expired",
  inactive: "Inactive",
  within: "Within budget",
  nearing: "Nearing limit",
  exceeded: "Exceeded",
  deactivated: "Deactivated",
} as const satisfies Record<KnownStatus, string>;

export const DOC_TYPE_LABELS = {
  project: "Project",
  requisition: "Requisition",
  purchase_order: "Purchase order",
  rfq: "RFQ",
  vendor: "Vendor",
  budget: "Budget",
  user: "User",
} as const satisfies Record<DocType, string>;

export const UNKNOWN_STATUS_LABEL = "Unknown status";

export function statusLabel(status: string): string | undefined {
  return (STATUS_LABELS as Readonly<Record<string, string>>)[status];
}
