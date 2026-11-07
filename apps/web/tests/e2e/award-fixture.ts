import type { Page } from "@playwright/test";
import { comparison, rfqId, stubComparison } from "./comparison-fixture";

const NOW = "2026-10-01T00:00:00Z";
const [A, B, C] = ["aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", "cccccccc-cccc-4ccc-8ccc-cccccccccccc"];
export { A, B, C, comparison, rfqId };

export const check = (over: Record<string, string> = {}) => ({ requisition_id: "r1", requisition: "REQ-1", scope: "100.00", funds: "100.00", award: "80.00", over_scope_by: "0.00", over_funds_by: "0.00", release: "20.00", main_project: "PRJ-1", ...over });
export const award = (over: Record<string, unknown> = {}) => ({ id: "aw1", number: "AWD-0001", rfq_id: rfqId, basis: "lowest_cost_supplier", status: "draft", rationale: null, version: 1, currency: "USD", rates: {}, evaluator_results: null, exceptions: null, created_by: rfqId, created_at: NOW, submitted_at: null, approved_at: null, total: "0.00", budget_total: "0.00", lines: [], checks: [], justification_required: true, ...over });
export const problem = (status: number, detail: string, extra: Record<string, unknown> = {}) => ({ type: "about:blank", title: "Problem", status, detail, correlation_id: "corr-1", ...extra });

export type AwardStub = { creates: { key?: string }[]; puts: { ifMatch?: string; lines: Record<string, unknown>[] }[]; patches: { rationale: string | null }[]; submits: number; gets: number };
export type Submit = "ok" | { status: number; body: unknown };

/** Stand-in for the award API on top of the comparison stub. `existing` is the award a GET returns; none means 404 until a POST creates it. */
export async function stubAward(page: Page, { existing = null, submit = "ok", checks = [check()], race = false, comparisonBody = comparison() }: { existing?: Record<string, unknown> | null; submit?: Submit; checks?: ReturnType<typeof check>[]; race?: boolean; comparisonBody?: unknown } = {}): Promise<AwardStub> {
  const state: AwardStub = { creates: [], puts: [], patches: [], submits: 0, gets: 0 };
  let current = existing;
  await stubComparison(page, { body: comparisonBody });
  const base = `/api/v1/sourcing/rfqs/${rfqId}/award`;
  await page.route(`**${base}**`, async route => {
    const req = route.request(), path = new URL(req.url()).pathname, m = req.method();
    const send = (data: unknown, code = 200) => route.fulfill({ status: code, contentType: "application/json", body: JSON.stringify(data) });
    if (path === base && m === "GET") { state.gets++; return current ? send(current) : send(problem(404, "No award."), 404); }
    if (path === base && m === "POST") {
      state.creates.push({ key: req.headers()["idempotency-key"] });
      if (race) { current = award(); return send(problem(409, "This RFQ already has an award. Reload it.", { checks: { problem: "award_exists" } }), 409); }
      current = award(); return send(current, 201);
    }
    if (path === `${base}/lines` && m === "PUT") {
      const lines = (req.postDataJSON() as { lines: Record<string, unknown>[] }).lines;
      state.puts.push({ ifMatch: req.headers()["if-match"], lines });
      current = { ...current!, version: (current!.version as number) + 1, total: lines.length ? "100.00" : "0.00", checks: lines.length ? checks : [],
        lines: lines.map((l, i) => ({ ...l, line_no: i + 1, bid_id: "b1", unit_price: "10.0000", currency: "USD", amount_vendor: "10.00", amount_rfq: "10.00", budget_amount: "10.00", not_lowest: l.vendor_id !== C && l.vendor_id !== B })) };
      return send(current);
    }
    if (path === base && m === "PATCH") {
      const { rationale } = req.postDataJSON() as { rationale: string | null };
      state.patches.push({ rationale });
      current = { ...current!, rationale, version: (current!.version as number) + 1 };
      return send(current);
    }
    if (path === `${base}/submit` && m === "POST") {
      state.submits++;
      if (submit !== "ok") return send(submit.body, submit.status);
      current = { ...current!, status: "approval_pending", version: (current!.version as number) + 1 };
      return send(current);
    }
    return route.fallback();
  });
  return state;
}
