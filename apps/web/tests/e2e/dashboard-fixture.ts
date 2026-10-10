import type { Page } from "@playwright/test";
import { projectId, project, unitId, stubProjects, firstActor } from "./projects-fixture";
export const childId = "55555555-5555-4555-8555-555555555555";
export async function stubDashboard(page: Page, drift = false) {
  const base = await stubProjects(page);
  const balance = { project_id: projectId, currency: "USD", allocated: "100.0000", reserved: "10.0000", committed: "20.0000", actual: "30.0000", available: "40.0000", consumption_pct: "60.0", variance: "40.0000", reconciles: !drift, as_of: "2026-10-10", range: { from: null, to: null } };
  const buckets = { allocated: "100.0000", reserved: "10.0000", committed: "20.0000", actual: "30.0000", available: "40.0000" };
  const nodeBalance = { reserved: "10.0000", committed: "20.0000", actual: "30.0000", allocated: "100.0000", consumed: "60.0000", available: "40.0000", currency: "USD" };
  const node = { id: childId, number: "PRJ-0002", name: "Cooling", status: "active", depth: 2, balance: nodeBalance, consumption_percent: "60.0", children: [] };
  const state = { drift, fail: false, empty: false, manyNodes: false, delay: 0, summaryRequests: [] as URLSearchParams[], ledgerRequests: [] as URLSearchParams[] };
  await page.route("**/api/v1/**", async route => {
    const url = new URL(route.request().url()); const path = url.pathname;
    if (state.delay && !path.includes("/auth/")) await new Promise(resolve => setTimeout(resolve, state.delay));
    const send = (data: unknown, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(data) });
    if (state.fail && (path.endsWith("/risk-indicators") || path.endsWith("/tree"))) return send({ detail: "Dashboard service unavailable. Retry.", status: 503 }, 503);
    if (path === `/api/v1/projects/${childId}`) return send({ ...project, id: childId, name: "Cooling" });
    if (path.endsWith("/balance/reconcile")) return send({ balance, entries_total_by_bucket: buckets, difference_by_bucket: { allocated: state.drift ? "-12.3456" : "0.0000", reserved: "0.0000", committed: "0.0000", actual: "0.0000" } });
    if (path.endsWith("/balance/aggregate")) return send({ mode: "roll_down", node_count: 2, totals: [{ currency: "USD", own: buckets, descendants: buckets, total: buckets }] });
    if (path.endsWith("/balance")) return send(state.empty ? { ...balance, allocated: "0.0000", available: "0.0000" } : balance);
    if (path.endsWith("/tree")) return send({ tree: { ...node, id: projectId, name: "Plant renewal", number: "PRJ-0001", depth: 1, children: state.empty ? [] : state.manyNodes ? Array.from({ length: 100 }, (_, i) => ({ ...node, id: `55555555-5555-4555-8555-${String(i).padStart(12, "0")}`, name: `Child ${i}` })) : [node] }, truncated: false });
    if (path.endsWith("/risk-indicators")) return send({ indicators: [{ code: "budget_variance", level: "low", facts: [{ label: "allocated", value: "100.0000" }, { label: "consumed", value: "60.0000" }, { label: "percent", value: "60.0" }] }, { code: "schedule", level: "low", facts: [{ label: "Schedule", value: "No overdue milestones" }] }, { code: "recorded_risks", level: "low", facts: [{ label: "Risks", value: "No open risks" }] }, { code: "requirements", level: "unknown", facts: [{ label: "Not available", value: "Requires requisitions (M2)" }] }] });
    if (path.endsWith("/ledger")) { state.ledgerRequests.push(url.searchParams); return send({ entries: [{ id: url.searchParams.has("cursor") ? 2 : 1, org_id: unitId, bu_id: unitId, project_id: projectId, entry_type: "allocation", bucket: "allocated", amount: "100.0000", currency: "USD", source_type: "manual", source_id: null, actor_id: firstActor, reason: "Initial allocation", reverses_entry_id: null, releases_entry_id: null }], next_cursor: url.searchParams.has("cursor") ? null : "page-2" }); }
    if (path === "/api/v1/budget/summary") { state.summaryRequests.push(url.searchParams); return send({ totals: state.empty ? [] : [{ currency: "USD", ...buckets }, { currency: "EUR", ...buckets }] }); }
    return route.fallback();
  });
  return { ...state, base, state };
}
