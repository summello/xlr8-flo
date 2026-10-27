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
import WorkflowList from "./admin/approval-workflows/list";
import WorkflowBuilder from "./admin/approval-workflows/[id]";
import VendorQuestionnaires from "./admin/vendor-questionnaires";
import ProjectDetail from "./projects/detail";
import Dashboard from "./projects/dashboard";
import BudgetOverview from "./budget";
import ApprovalInbox from "./approvals/inbox";
import ApprovalAvailability from "./approvals/availability";
import RequisitionList from "./requisitions/list";
import NewRequisition from "./requisitions/new";
import EditRequisition from "./requisitions/edit";
import ResubmitRequisition from "./requisitions/resubmit";
import AmendRequisition from "./requisitions/amend";
import AmendReview from "./requisitions/amend-review";
import RequisitionDetail from "./requisitions/detail";
import VendorList from "./vendors/list";
import VendorDetail from "./vendors/detail";
import NewVendor from "./vendors/new";
import EditVendor from "./vendors/edit";
import RfqList from "./sourcing/rfqs/list";
import RfqDetail from "./sourcing/rfqs/detail";
import RfqComparison from "./sourcing/rfqs/comparison";
import NewRfq from "./sourcing/rfqs/new";
import EditRfq from "./sourcing/rfqs/$id.edit";
import AmendRfq from "./sourcing/rfqs/$id.amend";
import RfqAward from "./sourcing/rfqs/$id.award";
import RfqEvaluation from "./sourcing/rfqs/$id.evaluation";
import { confirmLeave, consumeRestoring, installHistoryIndex, settlePop } from "../lib/navigation-guard";
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
const WORKFLOW_PATH = /^\/admin\/approval-workflows\/([0-9a-f-]{36})$/;
const PROJECT_PATH = /^\/projects\/([0-9a-f-]{36})$/;
const DASHBOARD_PATH = /^\/projects\/([0-9a-f-]{36})\/dashboard$/;
const REQUISITION_PATH = /^\/requisitions\/([0-9a-f-]{36})$/;
const REQUISITION_EDIT_PATH = /^\/requisitions\/([0-9a-f-]{36})\/edit$/;
const REQUISITION_RESUBMIT_PATH = /^\/requisitions\/([0-9a-f-]{36})\/resubmit$/;
const REQUISITION_AMEND_PATH = /^\/requisitions\/([0-9a-f-]{36})\/amend$/;
const REQUISITION_AMEND_REVIEW_PATH = /^\/requisitions\/([0-9a-f-]{36})\/amend\/review$/;
const VENDOR_PATH = /^\/vendors\/([0-9a-f-]{36})$/;
const VENDOR_EDIT_PATH = /^\/vendors\/([0-9a-f-]{36})\/edit$/;
const RFQ_PATH = /^\/sourcing\/rfqs\/([0-9a-f-]{36})$/;
const RFQ_COMPARISON_PATH = /^\/sourcing\/rfqs\/([0-9a-f-]{36})\/comparison$/;
const RFQ_AWARD_PATH = /^\/sourcing\/rfqs\/([0-9a-f-]{36})\/award$/;
const RFQ_EVALUATION_PATH = /^\/sourcing\/rfqs\/([0-9a-f-]{36})\/evaluation$/;
const RFQ_EDIT_PATH = /^\/sourcing\/rfqs\/([0-9a-f-]{36})\/edit$/;
const RFQ_AMEND_PATH = /^\/sourcing\/rfqs\/([0-9a-f-]{36})\/amend$/;
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
  "/admin/approval-workflows": { activeHref: "/administration", description: "Versioned approval workflows", hierarchy: [ORGANIZATION, ["/admin/approval-workflows", "Approval Workflows"]], phase: "foundation", title: "Approval Workflows" },
  "/admin/vendor-questionnaires": { activeHref: "/administration", description: "Versioned onboarding questionnaires for new vendors", hierarchy: [ORGANIZATION, ["/administration", "Administration"], ["/admin/vendor-questionnaires", "Vendor Questionnaires"]], phase: "foundation", title: "Vendor Questionnaires" },
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
    description: "Organization and unit budgets, grouped by currency.",
    hierarchy: [ORGANIZATION, ["/budget", "Budget"]],
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
    description: "Raise, track and withdraw requisitions.",
    hierarchy: [ORGANIZATION, ["/requisitions", "Requisitions"]],
    phase: "demand",
    title: "Requisitions",
  },
  "/requisitions/new": {
    activeHref: "/requisitions",
    description: "Raise a requisition, check its funds and submit it for approval.",
    hierarchy: [ORGANIZATION, ["/requisitions", "Requisitions"], ["/requisitions/new", "New Requisition"]],
    phase: "demand",
    title: "New Requisition",
  },
  "/approvals": {
    activeHref: "/approvals",
    description: "Decide the approvals waiting on you.",
    hierarchy: [ORGANIZATION, ["/approvals", "Approvals"]],
    phase: "demand",
    title: "Approval Inbox",
  },
  "/approvals/availability": {
    activeHref: "/approvals",
    description: "Pass your approvals to a colleague while you are away.",
    hierarchy: [ORGANIZATION, ["/approvals", "Approvals"], ["/approvals/availability", "Availability"]],
    phase: "demand",
    title: "Approval Availability",
  },
  "/vendors": {
    activeHref: "/vendors",
    description: "Find vendors, check who may be used here, and keep their documents current.",
    hierarchy: [ORGANIZATION, ["/vendors", "Vendors"]],
    phase: "commit",
    title: "Vendors",
  },
  "/vendors/new": {
    activeHref: "/vendors",
    description: "Ask for a new vendor to be approved.",
    hierarchy: [ORGANIZATION, ["/vendors", "Vendors"], ["/vendors/new", "Request Vendor"]],
    phase: "commit",
    title: "Request Vendor",
  },
  "/sourcing": {
    activeHref: "/sourcing",
    description: "What is open, what is due, and what needs you.",
    hierarchy: [ORGANIZATION, BUSINESS_UNIT, PROJECT, ["/sourcing", "Sourcing"]],
    phase: "commit",
    title: "Sourcing",
  },
  "/sourcing/rfqs": {
    activeHref: "/sourcing",
    description: "What is open, what is due, and what needs you.",
    hierarchy: [ORGANIZATION, ["/sourcing/rfqs", "RFQs"]],
    phase: "commit",
    title: "RFQs",
  },
  "/sourcing/rfqs/new": {
    activeHref: "/sourcing",
    description: "Choose approved requisition lines and the quantity of each to source.",
    hierarchy: [ORGANIZATION, ["/sourcing/rfqs", "RFQs"], ["/sourcing/rfqs/new", "New RFQ"]],
    phase: "commit",
    title: "New RFQ",
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
  if (WORKFLOW_PATH.test(path)) return { activeHref: "/administration", path, phase: "demand", title: "Approval Workflow", description: "Design and check approval routes.", breadcrumbs: [{ href: "/administration", label: "Administration" }, { href: path, label: "Approval Workflow" }] };
  if (PROJECT_PATH.test(path) || DASHBOARD_PATH.test(path)) return {
    activeHref: "/projects", path, phase: "plan", title: DASHBOARD_PATH.test(path) ? "Project Dashboard" : "Project", description: "Project details and actions.",
    breadcrumbs: [{ href: "/organization", label: "Organization" }, { href: "/projects", label: "Projects" }],
  };
  const screen = REQUISITION_RESUBMIT_PATH.test(path) ? ["Review Changes Before Resubmitting", "Review Changes"] : REQUISITION_AMEND_PATH.test(path) ? ["Amend Requisition", "Amend"] : REQUISITION_AMEND_REVIEW_PATH.test(path) ? ["Review Amendment", "Review Amendment"] : null;
  if (screen) return {
    activeHref: "/requisitions", path, phase: "demand", title: screen[0]!, description: "Review what changed, what it does to funding, and send it.",
    breadcrumbs: [{ href: "/organization", label: "Organization" }, { href: "/requisitions", label: "Requisitions" }, { href: path, label: screen[1]! }],
  };
  if (REQUISITION_PATH.test(path) || REQUISITION_EDIT_PATH.test(path)) return {
    activeHref: "/requisitions", path, phase: "demand", title: REQUISITION_EDIT_PATH.test(path) ? "Edit Requisition" : "Requisition", description: "Requisition details and actions.",
    breadcrumbs: [{ href: "/organization", label: "Organization" }, { href: "/requisitions", label: "Requisitions" }, { href: path, label: REQUISITION_EDIT_PATH.test(path) ? "Edit" : "Requisition" }],
  };
  if (VENDOR_PATH.test(path)) return {
    activeHref: "/vendors", path, phase: "commit", title: "Vendor", description: "Vendor profile, documents and history.",
    breadcrumbs: [{ href: "/organization", label: "Organization" }, { href: "/vendors", label: "Vendors" }, { href: path, label: "Vendor" }],
  };
  if (VENDOR_EDIT_PATH.test(path)) return {
    activeHref: "/vendors", path, phase: "commit", title: "Request Vendor", description: "Ask for a new vendor to be approved.",
    breadcrumbs: [{ href: "/organization", label: "Organization" }, { href: "/vendors", label: "Vendors" }, { href: path, label: "Request Vendor" }],
  };
  if (RFQ_PATH.test(path)) return {
    activeHref: "/sourcing", path, phase: "commit", title: "RFQ", description: "Where this RFQ is, and who has what.",
    breadcrumbs: [{ href: "/organization", label: "Organization" }, { href: "/sourcing/rfqs", label: "RFQs" }, { href: path, label: "RFQ" }],
  };
  if (RFQ_COMPARISON_PATH.test(path)) return {
    activeHref: "/sourcing", path, phase: "commit", title: "Bid Comparison", description: "Bids side by side, once the bidding stop time has passed.",
    breadcrumbs: [{ href: "/organization", label: "Organization" }, { href: "/sourcing/rfqs", label: "RFQs" }, { href: path.replace(/\/comparison$/, ""), label: "RFQ" }, { href: path, label: "Bid Comparison" }],
  };
  if (RFQ_AWARD_PATH.test(path)) return {
    activeHref: "/sourcing", path, phase: "commit", title: "Award", description: "A draft that must pass three checks before it goes for approval.",
    breadcrumbs: [{ href: "/organization", label: "Organization" }, { href: "/sourcing/rfqs", label: "RFQs" }, { href: path.replace(/\/award$/, ""), label: "RFQ" }, { href: path, label: "Award" }],
  };
  if (RFQ_EVALUATION_PATH.test(path)) return {
    activeHref: "/sourcing", path, phase: "commit", title: "Evaluation", description: "Declare any conflict first, then score each vendor.",
    breadcrumbs: [{ href: "/organization", label: "Organization" }, { href: "/sourcing/rfqs", label: "RFQs" }, { href: path.replace(/\/evaluation$/, ""), label: "RFQ" }, { href: path, label: "Evaluation" }],
  };
  if (RFQ_EDIT_PATH.test(path) || RFQ_AMEND_PATH.test(path)) {
    const editing = RFQ_EDIT_PATH.test(path), detail = path.replace(/\/(edit|amend)$/, "");
    return {
      activeHref: "/sourcing", path, phase: "commit", title: editing ? "Edit RFQ" : "Amend RFQ", description: editing ? "What exactly am I asking for, and from whom?" : "What changed, why, and who will be told?",
      breadcrumbs: [{ href: "/organization", label: "Organization" }, { href: "/sourcing/rfqs", label: "RFQs" }, { href: detail, label: "RFQ" }, { href: path, label: editing ? "Edit" : "Amend" }],
    };
  }
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
  const [requisitionHeading, setRequisitionHeading] = useState<{ path: string; title: string } | null>(null);
  const onRequisitionLoaded = useCallback((title: string) => { document.title = title; setRequisitionHeading({ path: window.location.pathname, title }); }, []);
  const verified = useRef(false);
  const [allowed, setAllowed] = useState(false);
  const [approvedRoute, setApprovedRoute] = useState<ShellRoute | null>(null);
  const [tenantName, setTenantName] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  const [route, setRoute] = useState(() => { installHistoryIndex(); return activateRoute(window.location.pathname); });

  useEffect(() => {
    const onPopState = (event: PopStateEvent) => {
      // UX-003: Back/Forward asks like in-app navigation; cancelling steps back with history.go().
      if (consumeRestoring() || !settlePop(event.state)) return;
      setAllowed(false); setFailed(false); setRoute(activateRoute(window.location.pathname)); };
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);

  const navigate = useCallback((href: string) => {
    if (!confirmLeave()) return; // UX-003: dirty forms ask before in-app navigation
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
  const workflowId = shellRoute.path.match(WORKFLOW_PATH)?.[1];
  const projectId = shellRoute.path.match(PROJECT_PATH)?.[1];
  const dashboardId = shellRoute.path.match(DASHBOARD_PATH)?.[1];
  const loadedHeading = projectHeading?.id === (projectId ?? dashboardId) ? projectHeading : null;
  const displayedRoute = loadedHeading ? { ...shellRoute, title: loadedHeading.title,
    breadcrumbs: [{ href: "/organization", label: "Organization" }, { href: `/organization/${loadedHeading.buId}`, label: loadedHeading.unit }, { href: shellRoute.path, label: `${loadedHeading.number} ${loadedHeading.title}` }] } : shellRoute;
  const requisitionId = shellRoute.path.match(REQUISITION_PATH)?.[1];
  const requisitionEditId = shellRoute.path.match(REQUISITION_EDIT_PATH)?.[1];
  const resubmitId = shellRoute.path.match(REQUISITION_RESUBMIT_PATH)?.[1];
  const amendId = shellRoute.path.match(REQUISITION_AMEND_PATH)?.[1];
  const amendReviewId = shellRoute.path.match(REQUISITION_AMEND_REVIEW_PATH)?.[1];
  const titledRoute = requisitionHeading?.path === shellRoute.path ? { ...displayedRoute, title: requisitionHeading.title, breadcrumbs: displayedRoute.breadcrumbs.map((item, i, all) => i === all.length - 1 ? { ...item, label: requisitionHeading.title } : item) } : displayedRoute;
  const adminUserId = shellRoute.path.match(ADMIN_USER_PATH)?.[1];
  const vendorId = shellRoute.path.match(VENDOR_PATH)?.[1];
  const vendorEditId = shellRoute.path.match(VENDOR_EDIT_PATH)?.[1];
  const rfqId = shellRoute.path.match(RFQ_PATH)?.[1];
  const comparisonRfqId = shellRoute.path.match(RFQ_COMPARISON_PATH)?.[1];
  const awardRfqId = shellRoute.path.match(RFQ_AWARD_PATH)?.[1];
  const editRfqId = shellRoute.path.match(RFQ_EDIT_PATH)?.[1];
  const evaluationRfqId = shellRoute.path.match(RFQ_EVALUATION_PATH)?.[1];
  const amendRfqId = shellRoute.path.match(RFQ_AMEND_PATH)?.[1];

  return (
    <AppShell navigate={navigate} route={{ ...titledRoute, breadcrumbs: titledRoute.breadcrumbs.map(item => ({ ...item, label: item.href === "/organization" && tenantName ? tenantName : item.label })) }}>
      {shellRoute.path === "/projects" ? <ProjectList navigate={navigate} /> : undefined}
      {shellRoute.path === "/vendors" ? <VendorList key={window.location.search} navigate={navigate} /> : undefined}
      {shellRoute.path === "/vendors/new" ? <NewVendor navigate={navigate} /> : undefined}
      {vendorEditId ? <EditVendor key={vendorEditId} id={vendorEditId} navigate={navigate} /> : undefined}
      {shellRoute.path === "/sourcing" || shellRoute.path === "/sourcing/rfqs" ? <RfqList key={window.location.search} navigate={navigate} /> : undefined}
      {rfqId ? <RfqDetail key={rfqId} id={rfqId} navigate={navigate} /> : undefined}
      {editRfqId ? <EditRfq key={editRfqId} id={editRfqId} navigate={navigate} /> : undefined}
      {amendRfqId ? <AmendRfq key={amendRfqId} id={amendRfqId} navigate={navigate} /> : undefined}
      {awardRfqId ? <RfqAward key={awardRfqId} id={awardRfqId} navigate={navigate} /> : undefined}
      {evaluationRfqId ? <RfqEvaluation key={evaluationRfqId} id={evaluationRfqId} navigate={navigate} /> : undefined}
      {comparisonRfqId ? <RfqComparison key={comparisonRfqId} id={comparisonRfqId} navigate={navigate} /> : undefined}
      {vendorId ? <VendorDetail key={vendorId} id={vendorId} navigate={navigate} /> : undefined}
      {shellRoute.path === "/requisitions" ? <RequisitionList key={window.location.search} navigate={navigate} /> : undefined}
      {shellRoute.path === "/requisitions/new" ? <NewRequisition navigate={navigate} /> : undefined}
      {shellRoute.path === "/sourcing/rfqs/new" ? <NewRfq key={window.location.search} navigate={navigate} /> : undefined}
      {requisitionId ? <RequisitionDetail key={requisitionId} id={requisitionId} navigate={navigate} onLoaded={onRequisitionLoaded} /> : undefined}
      {requisitionEditId ? <EditRequisition key={requisitionEditId} id={requisitionEditId} navigate={navigate} onLoaded={onRequisitionLoaded} /> : undefined}
      {resubmitId ? <ResubmitRequisition key={resubmitId} id={resubmitId} navigate={navigate} /> : undefined}
      {amendId ? <AmendRequisition key={amendId} id={amendId} navigate={navigate} /> : undefined}
      {amendReviewId ? <AmendReview key={amendReviewId} id={amendReviewId} navigate={navigate} /> : undefined}
      {shellRoute.path === "/projects/new" ? <NewProject navigate={navigate} /> : undefined}
      {dashboardId ? <Dashboard key={`${dashboardId}-${window.location.search}`} id={dashboardId} navigate={navigate} onLoaded={onProjectLoaded} /> : undefined}
      {shellRoute.path === "/approvals" ? <ApprovalInbox navigate={navigate} /> : undefined}
      {shellRoute.path === "/approvals/availability" ? <ApprovalAvailability navigate={navigate} /> : undefined}
      {shellRoute.path === "/budget" ? <BudgetOverview key={window.location.search} navigate={navigate} /> : undefined}
      {shellRoute.path === "/admin/approval-workflows" ? <WorkflowList navigate={navigate} /> : undefined}
      {shellRoute.path === "/admin/vendor-questionnaires" ? <VendorQuestionnaires /> : undefined}
      {workflowId ? <WorkflowBuilder key={workflowId} id={workflowId} navigate={navigate} /> : undefined}
      {projectId ? <ProjectDetail key={projectId} id={projectId} navigate={navigate} onLoaded={onProjectLoaded} /> : undefined}
      {shellRoute.path === "/_dev/status-gallery" ? <StatusGallery /> : undefined}
      {shellRoute.path === "/_dev/grid" ? <GridGallery /> : undefined}
      {shellRoute.path === "/_dev/forms" ? <FormGallery /> : undefined}
      {adminUserId === undefined ? undefined : <EffectiveAccessExplorer userId={adminUserId} />}
    </AppShell>
  );
}
