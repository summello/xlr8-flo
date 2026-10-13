import type { Page } from "@playwright/test";
import type { components } from "../../src/api/generated/schema";
import { instanceId, reqId, stubRequisitions } from "./requisitions-fixture";
import { projectId } from "./projects-fixture";

type Req = components["schemas"]["RequisitionRead"];
type Line = components["schemas"]["LineRead"];
export type ChangeSet = components["schemas"]["ChangeSet"];
export type Amendment = components["schemas"]["AmendmentRead"];
type Revision = components["schemas"]["RevisionSummary"];

export const quantityChange: ChangeSet = {
  from_revision: 1, to: "draft", material: true, nothing_changed: false, routing: "restarts",
  rows: [{ field: "lines/1/quantity", label: "Line 1 quantity", was: "10.0000", now: "9.0000", change: "changed", value_type: "quantity" },
    { field: "estimate", label: "Estimate", was: "196000.0000", now: "192500.0000", change: "changed", value_type: "money", delta: { amount: "3500.0000", direction: "down", percent: "2" } },
    { field: "lines/2", label: "Line 2", was: null, now: "Gasket", change: "added", value_type: "text" }],
  funding: { currency: "USD", outcome: "released_once", reserved_now: "196000.0000", reserved_after: "192500.0000", project_available_after: "43500.0000", note: null },
};
export const justificationChange: ChangeSet = {
  from_revision: 1, to: "draft", material: false, nothing_changed: false, routing: "same_step",
  rows: [{ field: "justification", label: "Justification", was: "End of life", now: "End of life, and unsafe", change: "changed", value_type: "text" }], funding: null,
};
export const revisions = (): Revision[] => [
  { revision_no: 1, kind: "submission", status: "returned", created_at: "2026-10-02T00:00:00Z", created_by: "bbbbbbbb-0000-4000-8000-000000000001", note: "First send" },
];
export function amendmentRead(patch: Partial<Amendment> = {}): Amendment {
  return {
    currency: "USD", estimate: "20.0000", revision_no: 2, status: "draft", version: 1,
    header: { title: "Press brake replacement", project_id: projectId, department_code: "D", ledger_account_code: "L", delivery_address_id: "aaaaaaaa-1111-4111-8111-111111111111", justification: "End of life", needed_by: "2030-01-31" },
    lines: [{ line_id: "cccccccc-0000-4000-8000-000000000001", line_no: 1, line_type: "item", description: "Part 1", quantity: "2.0000", uom_code: "ea", unit_price: "10.0000", amount: "20.0000", catalogue_item_id: null, category_code: null }],
    ...patch,
  };
}

export type Submit = "ok" | "nothing_changed" | "stale_version" | "invalid" | "funds";

/** Layers the E09-S08 routes (changes, revisions, amendments) over the E09-S11 requisition stub. Nothing here reaches a server. */
export async function stubChanges(page: Page, initial: { row: Req; lines?: Line[]; changes?: ChangeSet; amendment?: Amendment | null }) {
  const base = await stubRequisitions(page, { row: initial.row, lines: initial.lines ?? [] });
  const state = {
    base,
    changes: initial.changes ?? quantityChange,
    revisions: revisions(),
    amendment: initial.amendment ?? null as Amendment | null,
    submit: "ok" as Submit,
    changesFail: false,
    delay: 0,
    amendMaterial: false,
    sync: () => undefined as void,
    calls: [] as { path: string; search: string; method: string; body: Record<string, unknown> | null; headers: Record<string, string> }[],
  };
  const sync = () => { base.row = base.row ? { ...base.row, amendment: state.amendment ? { revision_no: state.amendment.revision_no, status: state.amendment.status as "draft" | "pending", can_continue: true } : null } : base.row; };
  state.sync = sync;
  sync();
  await page.route("**/api/v1/requisitions/**", async route => {
    const request = route.request(); const path = new URL(request.url()).pathname; const method = request.method();
    const body = request.postData() && request.headers()["content-type"]?.includes("json") ? request.postDataJSON() as Record<string, unknown> : null;
    const send = (value: unknown, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(value) });
    const problem = (detail: string, status: number, problemName: string, errors?: unknown[]) => send({ detail, status, correlation_id: "ref-4321", type: "about:blank", title: "Request failed", checks: { problem: problemName }, errors }, status);
    const prefix = `/api/v1/requisitions/${reqId}`;
    const mine = path === `${prefix}/changes` || path === `${prefix}/revisions` || path.startsWith(`${prefix}/amendments`) || (path === `${prefix}/transitions` && method === "POST");
    if (!mine) return route.fallback();
    state.calls.push({ path, search: new URL(request.url()).search, method, body, headers: request.headers() });
    if (state.delay) await new Promise(r => setTimeout(r, state.delay));
    if (path === `${prefix}/changes`) return state.changesFail ? problem("The changes service is unavailable.", 503, "unavailable") : send(state.changes);
    if (path === `${prefix}/revisions`) return send(state.revisions);
    const fail = (): Promise<void> | null => {
      if (state.submit === "nothing_changed") return problem("Not resubmitted. Nothing has changed.", 409, "nothing_changed");
      if (state.submit === "stale_version") return problem("The requisition changed. Reload it and retry.", 409, "stale_version");
      if (state.submit === "invalid") return problem("Fix things before you submit.", 422, "submission_invalid", [{ field: "required/justification", message: "Explain why it is needed" }]);
      if (state.submit === "funds") return problem("Fix things before you submit.", 422, "submission_invalid", [{ field: "funds/estimate", message: "Funds: a shortfall of 62,000.00 USD" }]);
      return null;
    };
    if (path === `${prefix}/transitions`) {
      if (body?.to !== "approval_pending") return route.fallback();
      const failed = fail(); if (failed) return failed;
      state.revisions = [...state.revisions, { revision_no: 2, kind: "submission", status: "pending", created_at: "2026-10-14T00:00:00Z", created_by: "bbbbbbbb-0000-4000-8000-000000000001", note: (body?.note as string) ?? null }];
      base.row = { ...base.row!, status: "approval_pending", version: base.row!.version + 1, approval: { instance_id: instanceId, status: "running" } };
      return send(base.row);
    }
    if (path === `${prefix}/amendments` && method === "POST") {
      if (!state.amendment) state.amendment = amendmentRead();
      sync();
      return send(state.amendment, 201);
    }
    if (path === `${prefix}/amendments/current`) return state.amendment ? send(state.amendment) : problem("Not found.", 404, "not_found");
    const amendPath = `${prefix}/amendments/${state.amendment?.revision_no ?? 2}`;
    if (path === amendPath && method === "PUT") {
      const incoming = (body?.lines as Record<string, unknown>[]) ?? [];
      const header = { ...state.amendment!.header, ...(body?.header as object) };
      state.amendment = amendmentRead({ header, version: state.amendment!.version + 1, lines: incoming.map((l, i) => ({ line_id: (l.line_id as string) ?? `cccccccc-2222-4000-8000-${String(i + 1).padStart(12, "0")}`, line_no: i + 1, line_type: (l.line_type as "item") ?? "item", description: String(l.description), quantity: String(l.quantity), uom_code: String(l.uom_code), unit_price: String(l.unit_price), amount: "0.0000", catalogue_item_id: null, category_code: null })) });
      return send(state.amendment);
    }
    if (path === amendPath && method === "DELETE") { state.amendment = null; sync(); return route.fulfill({ status: 204 }); }
    if (path === `${amendPath}/submit`) {
      const failed = fail(); if (failed) return failed;
      if (state.amendMaterial) { state.amendment = amendmentRead({ ...state.amendment!, status: "pending" }); state.revisions = [...state.revisions, { revision_no: 2, kind: "amendment", status: "pending", created_at: "2026-10-14T00:00:00Z", created_by: "bbbbbbbb-0000-4000-8000-000000000001", note: (body?.note as string) ?? null }]; }
      else { state.amendment = null; state.revisions = [...state.revisions, { revision_no: 2, kind: "amendment", status: "applied", created_at: "2026-10-14T00:00:00Z", created_by: "bbbbbbbb-0000-4000-8000-000000000001", note: null }] ; }
      sync();
      base.row = { ...base.row!, version: base.row!.version + 1, approval: state.amendMaterial ? { instance_id: instanceId, status: "running" } : base.row!.approval };
      return send(base.row);
    }
    return route.fallback();
  });
  return state;
}
