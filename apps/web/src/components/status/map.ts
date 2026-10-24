import type { StatusTone } from "./tones";

export const DOC_TYPES = [
  "project",
  "requisition",
  "purchase_order",
  "rfq",
  "award",
  "vendor",
  "budget",
  "user",
] as const;

export type DocType = (typeof DOC_TYPES)[number];

export const STATUS = {
  project: {
    draft: "neutral",
    approval_pending: "info",
    active: "success",
    deferred: "warning",
    completed: "neutral",
    abandoned: "danger",
  },
  requisition: {
    draft: "neutral",
    approval_pending: "info",
    sourcing_in_progress: "info",
    sourced: "info",
    awarded: "success",
    open_for_purchase: "info",
    completed: "success",
    rejected: "danger",
    withdrawn: "neutral",
    cancelled: "danger",
    expired: "warning",
  },
  purchase_order: {
    draft: "neutral",
    approval_pending: "info",
    approved: "info",
    issued: "success",
    partially_delivered: "info",
    delivered: "success",
    accepted: "success",
    closed: "neutral",
    cancelled: "danger",
    superseded: "warning",
  },
  rfq: {
    draft: "neutral",
    published: "info",
    closed: "info",
    under_evaluation: "info",
    awarded: "success",
    cancelled: "danger",
  },
  award: {
    draft: "neutral",
    approval_pending: "info",
    approved: "success",
    superseded: "warning",
    cancelled: "danger",
  },
  vendor: {
    draft: "neutral",
    pending_approval: "info",
    active: "success",
    suspended: "warning",
    inactive: "neutral",
    rejected: "danger",
  },
  budget: {
    within: "success",
    nearing: "warning",
    exceeded: "danger",
  },
  user: {
    active: "success",
    deactivated: "neutral",
  },
} as const satisfies Record<DocType, Readonly<Record<string, StatusTone>>>;

export type StatusFor<D extends DocType> = keyof (typeof STATUS)[D] & string;
export type KnownStatus = {
  [D in DocType]: StatusFor<D>;
}[DocType];
export type StatusSelection = {
  [D in DocType]: { docType: D; status: StatusFor<D> };
}[DocType];

export function statusTone(docType: DocType, status: string): StatusTone | undefined {
  const statuses: Readonly<Record<string, StatusTone>> = STATUS[docType];
  return statuses[status];
}

export function statusSelections(): StatusSelection[] {
  return DOC_TYPES.flatMap((docType) =>
    Object.keys(STATUS[docType]).map(
      (status) => ({ docType, status }) as StatusSelection,
    ),
  );
}
