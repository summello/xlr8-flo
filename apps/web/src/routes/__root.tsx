import { useCallback, useEffect, useState } from "react";

import AppShell, { type ShellRoute } from "../components/shell/AppShell";
import type { Phase } from "../components/command/registry";
import FormGallery from "./_dev/form-gallery";
import GridGallery from "./_dev/grid-gallery";
import StatusGallery from "./_dev/status-gallery";
import EffectiveAccessExplorer from "./admin/users/$id";
import type { RoutePath } from "./route-paths";

type RouteDefinition = {
  activeHref: string;
  description: string;
  hierarchy: readonly (readonly [string, string])[];
  phase: Phase;
  title: string;
};

const ORGANIZATION = ["/organization", "Northstar Capital"] as const;
const BUSINESS_UNIT = ["/organization/infrastructure", "Infrastructure BU"] as const;
const PROJECT = ["/projects/north-plant-renewal", "North plant renewal"] as const;
const SUB_PROJECT = ["/projects/north-plant-renewal/cooling", "Cooling system upgrade"] as const;
const ADMIN_USER_PATH = /^\/admin\/users\/([^/]+)$/;

const ROUTES: Readonly<Record<RoutePath, RouteDefinition>> = {
  "/": {
    activeHref: "/projects",
    description: "Choose a lifecycle module or press Command K or Control K to move anywhere.",
    hierarchy: [ORGANIZATION, BUSINESS_UNIT, ["/projects", "Projects"]],
    phase: "plan",
    title: "Projects",
  },
  "/organization": {
    activeHref: "/organization",
    description: "Organization screens arrive with the foundation milestone.",
    hierarchy: [ORGANIZATION],
    phase: "foundation",
    title: "Organization",
  },
  "/administration": {
    activeHref: "/administration",
    description: "Administration screens arrive with the foundation milestone.",
    hierarchy: [ORGANIZATION, ["/administration", "Administration"]],
    phase: "foundation",
    title: "Administration",
  },
  "/projects": {
    activeHref: "/projects",
    description: "Project screens arrive with the budget spine milestone.",
    hierarchy: [ORGANIZATION, BUSINESS_UNIT, ["/projects", "Projects"]],
    phase: "plan",
    title: "Projects",
  },
  "/projects/new": {
    activeHref: "/projects",
    description: "The project form arrives with the project management story.",
    hierarchy: [ORGANIZATION, BUSINESS_UNIT, ["/projects", "Projects"], ["/projects/new", "Create"]],
    phase: "plan",
    title: "Create project",
  },
  "/projects/north-plant-renewal": {
    activeHref: "/projects",
    description: "The project overview arrives with the project management story.",
    hierarchy: [ORGANIZATION, BUSINESS_UNIT, PROJECT],
    phase: "plan",
    title: "North plant renewal",
  },
  "/budget": {
    activeHref: "/budget",
    description: "Budget screens arrive with the budget spine milestone.",
    hierarchy: [ORGANIZATION, BUSINESS_UNIT, PROJECT, ["/budget", "Budget"]],
    phase: "plan",
    title: "Budget",
  },
  "/reporting": {
    activeHref: "/reporting",
    description: "Reporting screens arrive with the reporting milestone.",
    hierarchy: [ORGANIZATION, BUSINESS_UNIT, ["/reporting", "Reporting"]],
    phase: "plan",
    title: "Reporting",
  },
  "/requisitions": {
    activeHref: "/requisitions",
    description: "Requisition screens arrive with the demand milestone.",
    hierarchy: [ORGANIZATION, BUSINESS_UNIT, PROJECT, SUB_PROJECT, ["/requisitions", "Requisitions"]],
    phase: "demand",
    title: "Requisitions",
  },
  "/approvals": {
    activeHref: "/approvals",
    description: "Approval screens arrive with the demand milestone.",
    hierarchy: [ORGANIZATION, BUSINESS_UNIT, PROJECT, ["/approvals", "Approvals"]],
    phase: "demand",
    title: "Approvals",
  },
  "/vendors": {
    activeHref: "/vendors",
    description: "Vendor screens arrive with the commitment milestone.",
    hierarchy: [ORGANIZATION, ["/vendors", "Vendors"]],
    phase: "commit",
    title: "Vendors",
  },
  "/sourcing": {
    activeHref: "/sourcing",
    description: "Sourcing screens arrive with the commitment milestone.",
    hierarchy: [ORGANIZATION, BUSINESS_UNIT, PROJECT, ["/sourcing", "Sourcing"]],
    phase: "commit",
    title: "Sourcing",
  },
  "/purchase-orders": {
    activeHref: "/purchase-orders",
    description: "Purchase order screens arrive with the commitment milestone.",
    hierarchy: [ORGANIZATION, BUSINESS_UNIT, PROJECT, SUB_PROJECT, ["/purchase-orders", "Purchase orders"]],
    phase: "commit",
    title: "Purchase orders",
  },
  "/assets": {
    activeHref: "/assets",
    description: "Asset screens arrive with the commitment milestone.",
    hierarchy: [ORGANIZATION, BUSINESS_UNIT, PROJECT, ["/assets", "Assets"]],
    phase: "commit",
    title: "Assets",
  },
  "/help/keyboard": {
    activeHref: "/projects",
    description: "Press Command K or Control K for the command menu, or C to create a project.",
    hierarchy: [ORGANIZATION, ["/help/keyboard", "Keyboard help"]],
    phase: "foundation",
    title: "Keyboard help",
  },
  "/views/archive": {
    activeHref: "/projects",
    description: "The view archive action is available only while the Alt modifier is held.",
    hierarchy: [ORGANIZATION, ["/views/archive", "Archive view"]],
    phase: "foundation",
    title: "Archive current view",
  },
  "/_dev/status-gallery": {
    activeHref: "/projects",
    description: "Every closed document status rendered from the shared status vocabulary.",
    hierarchy: [ORGANIZATION, ["/_dev/status-gallery", "Status gallery"]],
    phase: "foundation",
    title: "Status gallery",
  },
  "/_dev/grid": {
    activeHref: "/projects",
    description: "The shared server-driven data grid fixture.",
    hierarchy: [ORGANIZATION, BUSINESS_UNIT, ["/_dev/grid", "Data grid"]],
    phase: "plan",
    title: "Data grid",
  },
  "/_dev/forms": {
    activeHref: "/projects",
    description: "The shared accessible form primitives and generated API error bridge.",
    hierarchy: [ORGANIZATION, ["/_dev/forms", "Forms kit"]],
    phase: "foundation",
    title: "Forms kit",
  },
};

function routeFor(path: string): ShellRoute {
  const adminUser = path.match(ADMIN_USER_PATH)?.[1];
  if (adminUser !== undefined) {
    return {
      activeHref: "/administration",
      breadcrumbs: [
        { href: ORGANIZATION[0], label: ORGANIZATION[1] },
        { href: "/administration", label: "Administration" },
        { href: "/administration/users", label: "Users" },
        { href: path, label: adminUser },
      ],
      description: "Inspect active and pending grants and the source of every permission.",
      path,
      phase: "foundation",
      title: "Effective access",
    };
  }
  const definition = ROUTES[path as RoutePath] ?? ROUTES["/"];
  return {
    ...definition,
    breadcrumbs: definition.hierarchy.map(([href, label]) => ({ href, label })),
    path,
  };
}

function activateRoute(path: string): ShellRoute {
  const route = routeFor(path);
  document.title = route.title;
  return route;
}

export default function RootRoute() {
  const [route, setRoute] = useState(() => activateRoute(window.location.pathname));

  useEffect(() => {
    const onPopState = () => setRoute(activateRoute(window.location.pathname));
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);

  const navigate = useCallback((href: string) => {
    if (window.location.pathname !== href) window.history.pushState(null, "", href);
    setRoute(activateRoute(href));
  }, []);
  const adminUserId = route.path.match(ADMIN_USER_PATH)?.[1];

  return (
    <AppShell navigate={navigate} route={route}>
      {route.path === "/_dev/status-gallery" ? <StatusGallery /> : undefined}
      {route.path === "/_dev/grid" ? <GridGallery /> : undefined}
      {route.path === "/_dev/forms" ? <FormGallery /> : undefined}
      {adminUserId === undefined ? undefined : <EffectiveAccessExplorer userId={adminUserId} />}
    </AppShell>
  );
}
