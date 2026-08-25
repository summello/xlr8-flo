export const ROUTE_PATHS = [
  "/",
  "/organization",
  "/administration",
  "/projects",
  "/projects/new",
  "/projects/north-plant-renewal",
  "/budget",
  "/reporting",
  "/requisitions",
  "/approvals",
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
