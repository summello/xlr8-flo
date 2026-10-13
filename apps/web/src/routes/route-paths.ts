export const ROUTE_PATHS = [
  "/",
  "/organization",
  "/administration",
  "/admin/approval-workflows",
  "/projects",
  "/projects/new",
  "/budget",
  "/reporting",
  "/requisitions",
  "/approvals",
  "/approvals/availability",
  "/vendors",
  "/sourcing",
  "/purchase-orders",
  "/assets",
  "/help/keyboard",
  "/views/archive",
  "/_dev/status-gallery",
  "/_dev/grid",
  "/_dev/forms",
] as const;

export type RoutePath = (typeof ROUTE_PATHS)[number];
