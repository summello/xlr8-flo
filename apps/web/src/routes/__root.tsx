import { useCallback, useEffect, useState } from "react";

import AppShell, { type ShellRoute } from "../components/shell/AppShell";
import type { Phase } from "../components/command/registry";
import StatusGallery from "./_dev/status-gallery";

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

const ROUTES: Readonly<Record<string, RouteDefinition>> = {
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
};

function routeFor(path: string): ShellRoute {
  const definition = ROUTES[path] ?? ROUTES["/"]!;
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

  return (
    <AppShell navigate={navigate} route={route}>
      {route.path === "/_dev/status-gallery" ? <StatusGallery /> : undefined}
    </AppShell>
  );
}
