import type { Page } from "@playwright/test";
import type { components } from "../../src/api/generated/schema";
import { projectId, stubProjects, unitId } from "./projects-fixture";

type Req = components["schemas"]["RequisitionRead"];
type Line = components["schemas"]["LineRead"];
type Reservation = NonNullable<Req["reservation"]>;
export const reqId = "66666666-6666-4666-8666-666666666666";
export const generatedProjectId = "77777777-7777-4777-8777-777777777777";
export const instanceId = "88888888-8888-4888-8888-888888888888";
const catalogueId = "99999999-9999-4999-8999-999999999999";
const addressId = "aaaaaaaa-1111-4111-8111-111111111111";

export function reservation(remaining: string, patch: Partial<Reservation> = {}): Reservation {
  return { entry_id: 1, reserved: remaining, released: "0.0000", remaining, currency: "USD", funding_round: 1, project_available: "40000.0000", ...patch };
}
export function requisition(patch: Partial<Req> = {}): Req {
  return {
    id: reqId, number: "REQ-00418", title: "Press brake replacement", status: "draft", standalone: false, project_id: projectId, project_number: "PRJ-0001", project_name: "Plant renewal",
    bu_id: unitId, bu_name: "Infrastructure", requester_id: "bbbbbbbb-0000-4000-8000-000000000001", requester_email: "dev@example.test", requester_name: null, justification: "End of life", needed_by: "2030-01-31",
    department_code: "D", ledger_account_code: "L", delivery_address_id: addressId, estimate: "0.0000", currency: "USD", version: 1,
    submitted_at: null, decided_at: null, created_at: "2026-10-01T00:00:00Z", updated_at: "2026-10-02T00:00:00Z", reservation: null, approval: null, amendment: null, can_edit: true, ...patch,
  };
}
export function lineRow(n: number, patch: Partial<Line> = {}): Line {
  return {
    id: `cccccccc-0000-4000-8000-${String(n).padStart(12, "0")}`, line_no: n, line_type: "item", description: `Part ${n}`, quantity: "2.0000", uom_code: "ea", unit_price: "10.0000", amount: "20.0000", currency: "USD",
    category_code: null, catalogue_item_id: null, requested_qty: "2.0000", sourced_qty: "2.0000", awarded_qty: "2.0000", ordered_qty: "1.0000", cancelled_qty: "0.0000", remaining_qty: "1.0000", ...patch,
  };
}

export type ReqState = Awaited<ReturnType<typeof stubRequisitions>>;
/** API-stubbed requisition screens (E06-S07 amendment 2): nothing here reaches a real server. */
export async function stubRequisitions(page: Page, initial: { row?: Req; lines?: Line[] } = {}) {
  await stubProjects(page);
  const state = {
    row: initial.row ?? null as Req | null,
    lines: initial.lines ?? [] as Line[],
    available: "100000.0000",
    submitProblems: null as { field: string; message: string }[] | null,
    submitStatus: 422,
    submitDetail: "",
    transitionFails: false,
    transitionConflict: false,
    transitionDelay: 0,
    getDelay: 0,
    withdrawAllowed: true,
    cancelAllowed: false,
    decisions: [] as components["schemas"]["DecisionRead"][],
    events: [] as components["schemas"]["StatusEventRead"][],
    links: [] as components["schemas"]["LinkedRecord"][],
    linksFail: false,
    catalogueFail: false,
    // The unit lookup lists only units where the caller holds project_request.create.
    canRequest: true,
    catalogue: [] as components["schemas"]["CatalogueItemRead"][],
    attachments: [] as components["schemas"]["AttachmentRead"][],
    list: [] as Req[],
    listFails: false,
    projectAvailable: "40000.0000" as string | null,
    calls: [] as { path: string; method: string; body: Record<string, unknown> | null; headers: Record<string, string>; query: URLSearchParams }[],
  };
  state.catalogue = [{ id: catalogueId, line_type: "item", description: "Drive motor", uom_code: "ea", category_code: null, last_unit_price: "18500.0000", currency: "USD", last_event: "purchased", last_event_at: "2026-03-12T00:00:00Z", last_vendor_id: null, last_vendor_name: "Voltek Drives", bu_id: unitId }];
  const estimateOf = () => state.lines.reduce((sum, l) => sum + BigInt(l.amount.replace(".", "")), 0n);
  const money4 = (cents4: bigint) => `${cents4 < 0n ? "-" : ""}${(cents4 < 0n ? -cents4 : cents4).toString().padStart(5, "0").replace(/(\d{4})$/, ".$1")}`;
  const toCents4 = (v: string) => { const [w, f = ""] = v.split("."); return BigInt(w + f.padEnd(4, "0")); };
  await page.route("**/api/v1/**", async route => {
    const request = route.request(); const url = new URL(request.url()); const path = url.pathname; const method = request.method();
    const body = request.postData() && request.headers()["content-type"]?.includes("json") ? request.postDataJSON() as Record<string, unknown> : null;
    const send = (value: unknown, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(value) });
    const problem = (detail: string, status: number, checks?: Record<string, string>, errors?: unknown[]) => send({ detail, status, correlation_id: "ref-4321", type: "about:blank", title: "Request failed", checks, errors }, status);
    const isReq = path.startsWith("/api/v1/requisitions") || path.startsWith("/api/v1/catalogue") || path.startsWith("/api/v1/documents/requisition") || path.startsWith("/api/v1/approvals/instances");
    if (!isReq) return route.fallback();
    state.calls.push({ path, method, body, headers: request.headers(), query: url.searchParams });
    const lookups: Record<string, { value: string; label: string; disabled?: boolean; note?: string }[]> = {
      project: [{ value: projectId, label: "PRJ-0001 Plant renewal" }, { value: generatedProjectId, label: "PRJ-0002 Cooling upgrade", disabled: true, note: "created from REQ-00277" }],
      bu: state.canRequest ? [{ value: unitId, label: "Infrastructure" }] : [], user: [{ value: "bbbbbbbb-1111-4111-8111-111111111111", label: "amara@example.test" }, { value: "bbbbbbbb-2222-4222-8222-222222222222", label: "lee@example.test" }], department: [{ value: "D", label: "D Operations" }], ledger_account: [{ value: "L", label: "L Plant equipment" }],
      uom: [{ value: "ea", label: "ea" }, { value: "day", label: "day" }], delivery_address: [{ value: addressId, label: "North plant dock" }],
    };
    const lookup = path.match(/^\/api\/v1\/requisitions\/lookups\/(.+)$/);
    if (lookup) return send({ rows: lookups[lookup[1]!] ?? [] });
    if (path === "/api/v1/catalogue") return state.catalogueFail ? problem("Catalogue unavailable.", 503) : send({ rows: state.catalogue });
    if (path === "/api/v1/requisitions" && method === "GET") {
      if (state.listFails) return problem("The service is unavailable.", 503);
      let rows = state.list;
      if (url.searchParams.get("status")) rows = rows.filter(r => r.status === url.searchParams.get("status"));
      const q = url.searchParams.get("q")?.toLowerCase(); if (q) rows = rows.filter(r => r.title.toLowerCase().includes(q) || r.number.toLowerCase().includes(q));
      const size = Number(url.searchParams.get("page_size") ?? 50);
      const from = Number(url.searchParams.get("cursor") ?? 0);
      const page = rows.slice(from, from + size);
      const sum = rows.reduce((t, r) => t + BigInt(r.estimate.replace(".", "")), 0n);
      return send({ rows: page, total: rows.length, next_cursor: from + size < rows.length ? String(from + size) : null, totals: rows.length ? [{ currency: "USD", amount: money4(sum) }] : [] });
    }
    if (path === "/api/v1/requisitions" && method === "POST") {
      state.row = requisition({ id: reqId, title: String(body?.title), project_id: (body?.project_id as string) ?? null, standalone: !body?.project_id, sponsor_ids: (body?.sponsor_ids as string[]) ?? [], justification: (body?.justification as string) ?? null, needed_by: (body?.needed_by as string) ?? null, department_code: (body?.department_code as string) ?? null, ledger_account_code: (body?.ledger_account_code as string) ?? null, delivery_address_id: (body?.delivery_address_id as string) ?? null, project_name: body?.project_id ? "Plant renewal" : null, project_number: body?.project_id ? "PRJ-0001" : null });
      return send(state.row, 201);
    }
    if (!state.row) return problem("Not found.", 404);
    if (path === `/api/v1/requisitions/${reqId}` && method === "GET") { if (state.getDelay) await new Promise(r => setTimeout(r, state.getDelay)); return send(state.row); }
    if (path === `/api/v1/requisitions/${reqId}` && method === "PATCH") { state.row = { ...state.row, ...body, version: state.row.version + 1 } as Req; return send(state.row); }
    if (path === `/api/v1/requisitions/${reqId}/lines` && method === "GET") return send({ rows: state.lines, estimate: money4(estimateOf()), currency: "USD", version: state.row.version });
    if (path === `/api/v1/requisitions/${reqId}/lines` && method === "PUT") {
      const incoming = (body?.lines as Record<string, unknown>[]);
      state.lines = incoming.map((l, i) => {
        const item = l.catalogue_item_id ? state.catalogue.find(c => c.id === l.catalogue_item_id) : undefined;
        const qty = String(l.quantity), price = String(l.unit_price);
        const amount = toCents4(qty) * toCents4(price) / 10000n;
        return lineRow(i + 1, { id: (l.line_id as string) ?? `cccccccc-1111-4000-8000-${String(i + 1).padStart(12, "0")}`, line_type: (item?.line_type ?? l.line_type) as Line["line_type"], description: item?.description ?? String(l.description), quantity: qty, unit_price: price, uom_code: item?.uom_code ?? String(l.uom_code), amount: money4(amount), requested_qty: qty, remaining_qty: qty, catalogue_item_id: (l.catalogue_item_id as string) ?? null });
      });
      state.row = { ...state.row, version: state.row.version + 1, estimate: money4(estimateOf()) };
      return send({ rows: state.lines, estimate: state.row.estimate, currency: "USD", version: state.row.version });
    }
    if (path === `/api/v1/requisitions/${reqId}/submission-check`) {
      const held = state.row.reservation?.remaining ?? "0.0000";
      const est = state.row.estimate;
      const after = money4(toCents4(state.available) - (toCents4(est) - toCents4(held)));
      return send({ ready: true, problems: [], funding: { available: state.available, this_requisition: est, already_reserved: held, available_after: after, currency: "USD", advisory: true },
        route: { path_edge_indices: [], separation_of_duty_applied: true, workflow_version_id: null, workflow_version: 1, problems: [], steps: [{ node_id: "n1", label: "Manager review", mode: "any", quorum: null, rule: "Any one", approver_emails: ["amara@example.test"], approver_names: ["Amara Okafor"], excluded: [], state: "upcoming", due_at: null, reached_via: null }] } });
    }
    if (path === `/api/v1/requisitions/${reqId}/transitions/available`) {
      const status = state.row.status;
      return send((["approval_pending", "withdrawn", "cancelled"] as const).map(to => ({ to, allowed: to === "withdrawn" ? state.withdrawAllowed && ["draft", "approval_pending", "sourcing_in_progress"].includes(status) : to === "cancelled" ? state.cancelAllowed : status === "draft",
        blocked_reasons: to === "withdrawn" && !state.withdrawAllowed ? ["Only the requester can withdraw. Ask an administrator to cancel it."] : [] })));
    }
    if (path === `/api/v1/requisitions/${reqId}` && method === "DELETE") return route.fulfill({ status: 204 });
    if (path === `/api/v1/requisitions/${reqId}/transitions` && method === "POST") {
      if (state.transitionDelay) await new Promise(r => setTimeout(r, state.transitionDelay));
      const to = String(body?.to);
      if (state.transitionFails) return problem("The service is unavailable.", 503);
      if (state.transitionConflict) return problem("This status transition is invalid. Choose an available action.", 409, { problem: "invalid_transition" });
      if (to === "approval_pending") {
        if (state.submitProblems) return problem("Fix things before you submit.", state.submitStatus, { problem: "submission_invalid" }, state.submitProblems);
        if (state.submitDetail) return problem(state.submitDetail, 409, { problem: "nothing_changed" });
        state.row = { ...state.row, status: "approval_pending", version: state.row.version + 1, reservation: reservation(state.row.estimate), approval: { instance_id: instanceId, status: "running" } };
        return send(state.row);
      }
      const held = state.row.reservation;
      state.row = { ...state.row, status: to as Req["status"], version: state.row.version + 1, reservation: held ? { ...held, released: held.remaining, remaining: "0.0000" } : null };
      return send(state.row);
    }
    if (path === `/api/v1/requisitions/${reqId}/events`) return send(state.events);
    if (path === `/api/v1/requisitions/${reqId}/links`) return state.linksFail ? problem("Links unavailable.", 503) : send({ nodes: state.links, edges: [], partial: false });
    if (path === `/api/v1/documents/requisition/${reqId}/attachments`) return method === "GET" ? send(state.attachments) : problem("Not supported in this fixture.", 400);
    if (path === `/api/v1/approvals/instances/${instanceId}/decisions`) return send(state.decisions);
    if (path === `/api/v1/approvals/instances/${instanceId}`) return send({ separation_of_duty_applied: true, id: instanceId, document_type: "requisition", document_id: reqId, workflow_version: 1, workflow_version_id: instanceId, document_version: 2, status: "running", requester_email: "dev@example.test", requester_name: state.row?.requester_name ?? null, current_node_id: "n1",
      route: [{ node_id: "n1", label: "Manager review", mode: "any", quorum: null, rule: "Any one", approver_emails: ["amara@example.test"], approver_names: ["Amara Okafor"], excluded: [], state: "open", due_at: null, reached_via: null }],
      tasks: [{ id: instanceId, node_id: "n1", label: "Manager review", mode: "any", quorum: null, status: "open", outcome: null, approver_emails: ["amara@example.test"], approver_names: ["Amara Okafor"], due_at: null, completed_at: null }], started_at: "2026-10-02T00:00:00Z", finished_at: null });
    return problem("Not found.", 404);
  });
  return state;
}
