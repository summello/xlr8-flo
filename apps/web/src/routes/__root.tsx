import { useCallback, useEffect, useRef, useState } from "react";

import Screens from "./sign-in";
import InviteScreen from "./invite";
import { destination, probe, safeReturn, organizationName } from "../features/auth/api";
import { apiClient } from "../api/client";

import AppShell, { type ShellRoute } from "../components/shell/AppShell";
import type { Phase } from "../components/command/registry";
import FormGallery from "./_dev/form-gallery";
import GridGallery from "./_dev/grid-gallery";
import StatusGallery from "./_dev/status-gallery";
import EffectiveAccessExplorer from "./admin/users/$id";
import ProjectList from "./projects/list";
import NewProject from "./projects/new";
import ProjectDetail from "./projects/detail";
import type { Project } from "../components/projects/api";
import type { RoutePath } from "./route-paths";

type RouteDefinition = {
  activeHref: string;
  description: string;
  hierarchy: readonly (readonly [string, string])[];
  phase: Phase;
  title: string;
};

const AUTH_PATHS: readonly string[] = ["/invite", "/sign-in", "/sign-in/mfa", "/sign-in/mfa/enroll", "/sign-in/organization"];

const ORGANIZATION = ["/organization", "Northstar Capital"] as const;
const BUSINESS_UNIT = ["/organization/infrastructure", "Infrastructure BU"] as const;
const PROJECT = ["/projects/north-plant-renewal", "North plant renewal"] as const;
const SUB_PROJECT = ["/projects/north-plant-renewal/cooling", "Cooling system upgrade"] as const;
const PROJECT_PATH = /^\/projects\/([0-9a-f-]{36})$/;
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
    description: "Search, filter and manage capital projects.",
    hierarchy: [ORGANIZATION, BUSINESS_UNIT, ["/projects", "Projects"]],
    phase: "plan",
    title: "Projects",
  },
  "/projects/new": {
    activeHref: "/projects",
    description: "Create a project and submit it for approval.",
    hierarchy: [ORGANIZATION, BUSINESS_UNIT, ["/projects", "Projects"], ["/projects/new", "Create"]],
    phase: "plan",
    title: "Create project",
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
  if (PROJECT_PATH.test(path)) return {
    activeHref: "/projects", path, phase: "plan", title: "Project", description: "Project details and actions.",
    breadcrumbs: [{ href: "/organization", label: "Organization" }, { href: "/projects", label: "Projects" }],
  };
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
  const [projectHeading, setProjectHeading] = useState<{ id: string; title: string; unit: string; number: string; buId: string } | null>(null);
  const onProjectLoaded = useCallback((project: Project, unit: string) => {
    document.title = project.name;
    setProjectHeading({ id: project.id, title: project.name, unit, number: project.number, buId: project.bu_id });
  }, []);
  const verified = useRef(false);
  const [allowed, setAllowed] = useState(false);
  const [approvedRoute, setApprovedRoute] = useState<ShellRoute | null>(null);
  const [tenantName, setTenantName] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  const [route, setRoute] = useState(() => activateRoute(window.location.pathname));

  useEffect(() => {
    const onPopState = () => { setAllowed(false); setFailed(false); setRoute(activateRoute(window.location.pathname)); };
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);

  const navigate = useCallback((href: string) => {
    setAllowed(false);
    if (AUTH_PATHS.includes(new URL(href, window.location.origin).pathname)) { verified.current = false; setApprovedRoute(null); }
    setFailed(false);
    if (window.location.pathname !== href) window.history.pushState(null, "", href);
    setRoute(activateRoute(new URL(href, window.location.origin).pathname));
  }, []);
  useEffect(() => {
    if (route.path === "/invite" || /^\/invite\/[^/]+$/.test(route.path)) return;
    let active = true;
    void probe().then(access => {
      if (!active) return;
      const auth = AUTH_PATHS.includes(route.path);
      if (access === 'error') { setFailed(true); return; }
      if (!auth) {
        if (access === 'verified') { verified.current = true; setApprovedRoute(route); setAllowed(true);
          void apiClient.GET('/api/v1/auth/organizations').then(({ data }) => {
            if (!active || !data) return;
            const current = data.items.find(item => item.org_id === data.current_org_id);
            if (current) setTenantName(organizationName(current));
          });
        }
        else navigate(`${destination(access, '/')}?return=${encodeURIComponent(safeReturn(window.location.pathname + window.location.search + window.location.hash))}`);
      } else if (route.path !== '/sign-in' && access === 'signin') {
        navigate(`/sign-in?return=${encodeURIComponent(safeReturn(new URLSearchParams(window.location.search).get('return')))}`);
      } else if (route.path === '/sign-in' && access !== 'signin') {
        const back = safeReturn(new URLSearchParams(window.location.search).get('return'));
        navigate(destination(access, back) + (access === 'verified' ? '' : `?return=${encodeURIComponent(back)}`));
      }
    });
    return () => { active = false; };
  }, [route, navigate]);
  useEffect(() => {
    const ended = (response: Response) => {
      if (verified.current && response.status === 401 && !AUTH_PATHS.includes(window.location.pathname)) {
        navigate(`/sign-in?ended=1&return=${encodeURIComponent(safeReturn(window.location.pathname + window.location.search + window.location.hash))}`);
      }
    };
    const middleware = { onResponse({ response }: { response: Response }) { ended(response); } };
    apiClient.use(middleware);
    // Existing features use native fetch as well as the generated client. Observe
    // same-origin API responses from both without changing their request contracts.
    const original = window.fetch;
    const observed: typeof window.fetch = async (...args) => {
      const response = await original(...args);
      const input = args[0];
      const url = new URL(input instanceof Request ? input.url : String(input), window.location.href);
      if (url.origin === window.location.origin && url.pathname.startsWith('/api/')) ended(response);
      return response;
    };
    window.fetch = observed;
    return () => {
      apiClient.eject(middleware);
      if (window.fetch === observed) window.fetch = original;
    };
  }, [navigate]);
  if (route.path === "/invite" || /^\/invite\/[^/]+$/.test(route.path)) return <InviteScreen navigate={navigate} />;
  if (AUTH_PATHS.includes(route.path)) return <Screens key={route.path} path={route.path} navigate={navigate} probeFailed={failed} />;
  if (failed) return <main><h1>We could not reach the server</h1><p>Your page is kept. Check your connection and try again.</p><button onClick={() => window.location.reload()}>Try again</button></main>;
  if (!allowed && !approvedRoute) return <main role="status">Checking your session</main>;
  // Keep the last authorized shell mounted during the probe; no new route is rendered early.
  const shellRoute = allowed ? route : approvedRoute!;
  const projectId = shellRoute.path.match(PROJECT_PATH)?.[1];
  const loadedHeading = projectHeading?.id === projectId ? projectHeading : null;
  const displayedRoute = loadedHeading ? { ...shellRoute, title: loadedHeading.title,
    breadcrumbs: [{ href: "/organization", label: "Organization" }, { href: `/organization/${loadedHeading.buId}`, label: loadedHeading.unit }, { href: shellRoute.path, label: `${loadedHeading.number} ${loadedHeading.title}` }] } : shellRoute;
  const adminUserId = shellRoute.path.match(ADMIN_USER_PATH)?.[1];

  return (
    <AppShell navigate={navigate} route={{ ...displayedRoute, breadcrumbs: displayedRoute.breadcrumbs.map(item => ({ ...item, label: item.href === "/organization" && tenantName ? tenantName : item.label })) }}>
      {shellRoute.path === "/projects" ? <ProjectList navigate={navigate} /> : undefined}
      {shellRoute.path === "/projects/new" ? <NewProject navigate={navigate} /> : undefined}
      {projectId ? <ProjectDetail key={projectId} id={projectId} onLoaded={onProjectLoaded} /> : undefined}
      {shellRoute.path === "/_dev/status-gallery" ? <StatusGallery /> : undefined}
      {shellRoute.path === "/_dev/grid" ? <GridGallery /> : undefined}
      {shellRoute.path === "/_dev/forms" ? <FormGallery /> : undefined}
      {adminUserId === undefined ? undefined : <EffectiveAccessExplorer userId={adminUserId} />}
    </AppShell>
  );
}
