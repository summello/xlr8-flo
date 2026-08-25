import type { StatusTone } from "./tones";

export const DOC_TYPES = [
  "project",
  "requisition",
  "purchase_order",
  "rfq",
  "vendor",
  "budget",
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
    cancelled: "danger",
  },
  purchase_order: {
    draft: "neutral",
    approval_pending: "info",
    approved: "info",
    issued: "success",
    partially_fulfilled: "info",
    fulfilled: "success",
    closed: "neutral",
    cancelled: "danger",
    superseded: "warning",
  },
  rfq: {
    draft: "neutral",
    published: "info",
    closed: "neutral",
    under_evaluation: "info",
    awarded: "success",
    cancelled: "danger",
  },
  vendor: {
    pending: "info",
    active: "success",
    suspended: "warning",
    expired: "danger",
    inactive: "neutral",
  },
  budget: {
    within: "success",
    nearing: "warning",
    exceeded: "danger",
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
