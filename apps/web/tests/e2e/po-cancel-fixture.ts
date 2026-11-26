import type { Page } from "@playwright/test";
import { poId, stubPo } from "./po-screens-fixture";

const NOW = "2026-10-12T10:00:00Z";
const projectId = "66666666-6666-4666-8666-666666666666";

type Options = { status?: string; actions?: string[]; nullEffectCodes?: boolean; fail?: { status: number; body: Record<string, unknown> } };
export type Sent = { method: string; path: string; headers: Record<string, string>; body: Record<string, unknown> };

/** `stubPo` plus the three routes of E13-S19, registered later so they win; everything else falls back to it. Figures are deliberately odd: the dialog must show the server's. */
export async function stubCancel(page: Page, { status = "issued", actions = ["cancel", "reduce"], nullEffectCodes = false, fail }: Options = {}) {
  await stubPo(page, { status });
  const state = { status, sent: [] as Sent[], fail };
  const lines = [1, 2, 3].map(n => ({ line_no: n, rfq_line_id: `r${n}`, requisition_line_id: `q${n}`, description: `Pump ${n}`, uom_code: "EA", quantity: "4.0000", unit_price: "100.0000", project_id: projectId, department_code: "OPS", ledger_account_code: "6100", price: "400.0000", tax: "0.0000", freight: "0.0000", line_total: "400.0000", budget_amount: "400.0000", budget_amount_project: "400.0000", value: "400.0000", value_converted: "400.0000" }));
  const unit = "55555555-5555-4555-8555-555555555555", detail = {
    id: poId, number: "PO-0007", unit_id: unit, vendor_id: "44444444-4444-4444-8444-444444444444", vendor_snapshot: { trading_name: "Acme Tooling" }, requisition_id: "33333333-3333-4333-8333-333333333333", award_id: "a1", currency: "USD", converted_currency: "USD",
    rate: { rate: "1", to: "USD" }, bill_to: { line1: "1 Main St", city: "Springfield", country: "US" }, ship_to_address_id: null, recipients: [], requester_id: unit,
    terms: "Net 30", needed_by: null, revision: 1, supersedes_id: null, created_at: NOW, freight_mode: "line", document_freight: null,
    price_total: "1200.0000", tax_total: "0.0000", freight_total: "0.0000", total: "1200.0000", budget_total: "1200.0000", budget_total_project: "1200.0000", total_converted: "1200.0000", vendor_status: "active",
  };
  const ref = (code: string, name: string) => ({ id: code, code, name });
  const preview = () => ({ status: state.status, released: state.status === "issued" ? "1234.5600" : "0.0000", released_currency: state.status === "issued" ? "USD" : null, rereserved: state.status === "issued", requisition_number: "REQ-0042", reservation_project: state.status === "issued" ? ref("SUB-7", "Sub Seven") : null, main_project: ref("MAIN-3", "Main Three") });
  await page.route("**/api/v1/purchase-orders/**", async route => {
    const req = route.request(), path = new URL(req.url()).pathname, method = req.method();
    const send = (data: unknown, code = 200) => route.fulfill({ status: code, contentType: "application/json", body: JSON.stringify(data) });
    const base = `/api/v1/purchase-orders/${poId}`;
    if (method === "GET" && path === base) {
      return send({ ...detail, version: state.status === "cancelled" ? 4 : 3, status: state.status, lines, actions: state.status === "cancelled" ? [] : actions, released_amount: state.status === "cancelled" ? "1234.5600" : null, released_currency: state.status === "cancelled" ? "USD" : null });
    }
    if (method === "GET" && path === `${base}/history` && state.status === "cancelled") return send({ entries: [{ occurred_at: NOW, actor: "buyer@example.test", action: "purchase_order.cancel", detail: "Status: cancelled" }], truncated: false });
    if (method === "GET" && path === `${base}/cancel-preview`) return send(preview());
    if (method === "POST" && (path === `${base}/cancel` || path === `${base}/reduce`)) {
      state.sent.push({ method, path, headers: req.headers(), body: req.postDataJSON() });
      if (state.fail) return send(state.fail.body, state.fail.status);
      if (path.endsWith("/cancel")) { state.status = "cancelled"; return send({ status: "cancelled" }); }
      return send({ id: "c2", number: 2, status: "approval_pending", budget_effect: [{ project_id: projectId, delta: "-123.4500", currency: "USD", department_code: nullEffectCodes ? null : "OPS", ledger_account_code: nullEffectCodes ? null : "6100" }] }, 201);
    }
    return route.fallback();
  });
  return state;
}
