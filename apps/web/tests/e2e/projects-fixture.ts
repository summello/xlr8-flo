import type { Page } from "@playwright/test";
import type { components } from "../../src/api/generated/schema";
export const projectId = "11111111-1111-4111-8111-111111111111";
export const unitId = "22222222-2222-4222-8222-222222222222";
export const firstActor = "33333333-3333-4333-8333-333333333333";
export const secondActor = "44444444-4444-4444-8444-444444444444";
export const project: components["schemas"]["ProjectRead"] = {
  id: projectId, external_ref: null, number: "PRJ-0001", name: "Plant renewal", status: "draft", bu_id: unitId, bu_name: "Infrastructure",
  parent_id: null, owner_id: firstActor, sponsor_id: null, description: "Renew the plant", department_code: "D", ledger_account_code: "L", currency: "USD",
  planned_start: "2026-10-01", planned_end: "2026-12-31", health: "unknown", percent_complete: 0, actual_start: null, actual_end: null, schedule_variance_days: null,
  version: 1, created_at: "2026-10-01T00:00:00Z", allocated: "1234.5000", available: "-0.0050",
};
export type Fixture = Awaited<ReturnType<typeof stubProjects>>;
export async function stubProjects(page: Page, settings: { total?: number; rows?: components["schemas"]["ProjectRead"][]; transitions?: components["schemas"]["TransitionAvailable"][] } = {}) {
  const state = {
    actor: firstActor, row: { ...project }, created: false, failCreate: false, stale: false, overrideDenied: false,
    requests: [] as { path: string; method: string; query: URLSearchParams; body: Record<string, unknown> | null; headers: Record<string, string> }[],
    phases: [] as components["schemas"]["PhaseRead"][], milestones: [] as components["schemas"]["MilestoneRead"][], risks: [] as components["schemas"]["RiskRead"][],
    fail: new Set<string>(),
  };
  const unit = { id: unitId, name: "Infrastructure", code: "INFRA", kind: "bu", parent_id: null, active: true, created_at: "2026-10-01T00:00:00Z" };
  const edges: Record<string, { to: components["schemas"]["ProjectRead"]["status"]; reason: boolean }[]> = {
    draft: [{ to: "approval_pending", reason: false }, { to: "abandoned", reason: false }],
    approval_pending: [{ to: "active", reason: false }, { to: "draft", reason: true }],
    active: [{ to: "deferred", reason: true }, { to: "completed", reason: false }, { to: "abandoned", reason: true }],
    deferred: [{ to: "active", reason: false }, { to: "abandoned", reason: true }], completed: [], abandoned: [],
  };
  await page.route("**/api/v1/**", async route => {
    const request = route.request(); const url = new URL(request.url()); const path = url.pathname; const method = request.method();
    const body = request.postData() ? request.postDataJSON() as Record<string, unknown> : null;
    state.requests.push({ path, method, query: url.searchParams, body, headers: request.headers() });
    const send = (value: unknown, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(value) });
    const problem = (detail: string, status: number, checks?: Record<string, string>) => send({ detail, status, correlation_id: "fixture", type: "about:blank", title: "Request failed", checks }, status);
    if (state.fail.has(path)) return problem("The service is unavailable. Your loaded data is preserved; retry.", 503);
    if (path === "/api/v1/auth/sessions") return send([]);
    if (path === "/api/v1/auth/organizations") return send({ items: [], current_org_id: null });
    if (path === "/api/v1/auth/me") return send({ identity_id: state.actor });
    if (path === "/api/v1/org/units") return send({ rows: [unit], next_cursor: null });
    if (path === `/api/v1/org/units/${unitId}`) return send(unit);
    if (path === "/api/v1/master/currency") return send([{ code: "USD", exponent: 2 }, { code: "JPY", exponent: 0 }]);
    if (path.startsWith("/api/v1/master/")) return send({ rows: [{ id: unitId, code: path.endsWith("department") ? "D" : "L", name: "Master code", active: true }], next_cursor: null });
    if (path === "/api/v1/projects" && method === "POST") {
      if (state.failCreate) return send({ status: 422, detail: "Unknown or inactive department code. Choose an active code.", errors: [{ field: "department_code", message: "Unknown or inactive department code." }] }, 422);
      state.row = { ...state.row, ...body } as typeof state.row; state.created = true;
      return send(state.row, 201);
    }
    if (path === "/api/v1/projects") {
      const total = settings.total ?? settings.rows?.length ?? 1;
      const offset = Number(url.searchParams.get("cursor") ?? "0");
      let rows = settings.rows ?? (settings.total ? Array.from({ length: Math.min(50, total - offset) }, (_, i) => ({ ...state.row, id: `00000000-0000-4000-8000-${String(offset + i).padStart(12, "0")}`, number: `PRJ-${String(offset + i).padStart(5, "0")}`, name: `Project ${offset + i}` })) : [state.row]);
      const status = url.searchParams.get("status"); if (status) rows = rows.filter(r => r.status === status);
      const q = url.searchParams.get("q"); if (q) rows = rows.filter(r => r.name.toLowerCase().startsWith(q.toLowerCase()) || r.number.toLowerCase().startsWith(q.toLowerCase()));
      if (url.searchParams.get("group_by") === "status") rows = [...rows].sort((a, b) => a.status.localeCompare(b.status) || a.number.localeCompare(b.number));
      return send({ rows, total: settings.total ? total : rows.length, next_cursor: settings.total && offset + 50 < total ? String(offset + 50) : null });
    }
    if (path === `/api/v1/projects/${projectId}`) return send(state.row);
    if (path.endsWith("/transitions/available")) {
      if (settings.transitions) return send(settings.transitions);
      return send((["draft", "approval_pending", "active", "deferred", "completed", "abandoned"] as const).map(to => {
        const edge = edges[state.row.status]!.find(e => e.to === to);
        const maker = state.row.status === "approval_pending" && to === "active" && state.actor === firstActor;
        return { to, reachable: !!edge, reason_required: !!edge?.reason, override_available: false, allowed: !!edge && !maker, blocked_reasons: maker ? ["The submitter cannot approve this project. Ask a different approver."] : edge ? [] : ["Invalid transition."] };
      }));
    }
    if (path.endsWith("/transitions")) {
      if (state.stale) return problem("The project changed. Reload project; your reason is preserved.", 409, { problem: "stale_version" });
      if (state.overrideDenied && body?.override) return problem("You need project.close.override to override closure. Ask an authorized closer.", 403);
      if (body?.to === "active" && state.actor === firstActor && state.row.status === "approval_pending") return problem("The submitter cannot approve this project. Ask a different approver.", 403);
      state.row = { ...state.row, status: body?.to as typeof state.row.status, version: state.row.version + 1 };
      return send(state.row);
    }
    if (path.endsWith("/balance")) return send({ project_id: projectId, currency: "USD", allocated: "1234.5000", reserved: "100.0000", committed: "200.0000", actual: "300.0000", available: "634.5000", consumption_pct: "48.60", variance: "0.0000", reconciles: true, as_of: "2026-10-01T00:00:00Z", range: { from: null, to: null } });
    for (const kind of ["phases", "milestones", "risks"] as const) {
      if (!path.includes(`/${kind}`)) continue;
      if (method === "GET") return send(kind === "risks" ? { rows: state.risks, next_cursor: null } : state[kind]);
      const editing = method === "PATCH";
      const id = editing ? path.split("/").at(-1)! : `55555555-5555-4555-8555-${String(state[kind].length + 1).padStart(12, "0")}`;
      const row = { id, project_id: projectId, created_at: "2026-10-01T00:00:00Z", schedule_variance_days: null, ...body, version: 1, score: Number(body?.likelihood) * Number(body?.impact), overdue: false };
      const records = state[kind] as unknown[];
      if (editing) { const index = records.findIndex(r => (r as { id: string }).id === id); records[index] = row; } else records.push(row);
      return send(row, editing ? 200 : 201);
    }
    return route.fallback();
  });
  return state;
}
