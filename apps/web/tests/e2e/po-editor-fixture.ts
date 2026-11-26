import type { Page } from "@playwright/test";

export const poId = "11111111-1111-4111-8111-111111111111";
const unitId = "55555555-5555-4555-8555-555555555555";
const reqId = "33333333-3333-4333-8333-333333333333";
const vendorId = "44444444-4444-4444-8444-444444444444";
const projectId = "66666666-6666-4666-8666-666666666666";
const shipA = "77777777-7777-4777-8777-777777777771";
const shipB = "77777777-7777-4777-8777-777777777772";
const NOW = "2026-10-12T10:00:00Z";

export type Recorded = { method: string; path: string; headers: Record<string, string>; body: unknown };
type Options = {
  actions?: string[]; taxConsumes?: boolean | null; status?: string;
  /** Scripted answers: the PATCH status (200 or 422) and the submit status (200 or 409 with a problem). */
  patch?: "ok" | "invalid"; submit?: "ok" | "workflow";
};

const line = (n: number, over: Record<string, string> = {}) => ({ line_no: n, rfq_line_id: `r${n}`, requisition_line_id: `q${n}`, description: `Pump ${n}`, uom_code: "EA", quantity: "4.0000", unit_price: "100.0000", project_id: projectId, department_code: "OPS", ledger_account_code: "6100", price: "400.0000", tax: "40.0000", freight: "10.0000", line_total: "450.0000", budget_amount: "450.0000", budget_amount_project: "450.0000", value: "450.0000", value_converted: "450.0000", ...over });

/** An in-memory PO API for the editor. Every response figure is deliberately odd: the page must show the server's, never its own sum. */
export async function stubEditor(page: Page, { actions = ["edit", "submit", "delete", "cancel"], taxConsumes = true, status = "draft", patch = "ok", submit = "ok" }: Options = {}) {
  const state = { version: 3, calls: [] as Recorded[], notes: [] as { id: string; body: string; visibility: string; author_name: string; author_email: string; created_at: string }[], marks: { f1: false } };
  let mode = "line";
  const po = () => ({
    id: poId, number: "PO-0007", unit_id: unitId, vendor_id: vendorId, vendor_snapshot: { trading_name: "Acme Tooling", eligibility: { eligible: true } }, requisition_id: reqId, award_id: "a1", currency: "USD", converted_currency: "USD",
    rate: { rate: "1", to: "USD" }, bill_to: { line1: "1 Main St", city: "Springfield", country: "US" }, ship_to_address_id: shipA, recipients: [{ name: "Dock", email: "dock@example.test" }], requester_id: unitId,
    terms: "Net 30", needed_by: null, status, version: state.version, revision: 0, supersedes_id: null, created_at: NOW, freight_mode: mode, document_freight: mode === "document" ? "12.3400" : null,
    lines: [line(1, mode === "document" ? { freight: "7.7700" } : {}), line(2, mode === "document" ? { freight: "4.5700" } : {})],
    price_total: "801.0000", tax_total: "79.5000", freight_total: mode === "document" ? "12.3400" : "20.0000", total: "913.2500", budget_total: "913.2500", budget_total_project: "913.2500", total_converted: "913.2500",
    released_amount: null, released_currency: null, vendor_status: "active", actions,
    tax_consumes_budget: taxConsumes, freight_consumes_budget: null,
  });
  await page.route("**/api/v1/**", async route => {
    const req = route.request(), url = new URL(req.url()), path = url.pathname, method = req.method();
    const send = (data: unknown, code = 200) => route.fulfill({ status: code, contentType: "application/json", body: JSON.stringify(data) });
    const base = `/api/v1/purchase-orders/${poId}`;
    if (path === "/api/v1/auth/sessions" || path === "/api/v1/master/currency") return send([]);
    if (path === "/api/v1/auth/me") return send({ identity_id: unitId });
    if (path === "/api/v1/auth/organizations") return send({ items: [], current_org_id: unitId });
    if (method !== "GET") state.calls.push({ method, path, headers: req.headers(), body: req.postDataJSON() });
    if (path === base && method === "GET") return send(po());
    if (path === base && method === "PATCH") {
      if (patch === "invalid") return send({ title: "Unprocessable", status: 422, detail: "Check the fields.", correlation_id: "c", errors: [{ field: "lines/1/quantity", message: "Value error, Quantity exceeds the open quantity." }] }, 422);
      const body = req.postDataJSON() as { freight_mode?: string };
      if (body.freight_mode) mode = body.freight_mode;
      state.version += 1;
      return send(po());
    }
    if (path === `${base}/submit` && method === "POST") {
      if (submit === "workflow") return send({ title: "Conflict", status: 409, detail: "No workflow.", correlation_id: "c", checks: { problem: "workflow_not_set_up" } }, 409);
      return send(po());
    }
    if (path === `${base}/budget-summary`) return send({ projects: [{ project_id: projectId, project_code: "P-100", project_name: "Plant 4", currency: "USD", committed: "5000.0000", this_po: "17.0300", this_po_state: "reserved", available_after_issue: "1.0000", actual: "250.0000" }] });
    if (path === `${base}/ship-to-options`) return send([{ id: shipA, kind: "ship_to", line1: "9 Dock Rd", city: "Springfield", country: "US" }, { id: shipB, kind: "ship_to", line1: "2 Yard Ln", city: "Shelbyville", country: "US" }]);
    if (path === `/api/v1/vendors/${vendorId}/eligibility`) return send({ eligible: true, reasons: [], warnings: [] });
    if (path === `/api/v1/documents/purchase_order/${poId}/notes`) {
      if (method === "POST") { const b = req.postDataJSON() as { body: string; visibility: string }; state.notes.push({ id: `n${state.notes.length}`, ...b, author_name: "Buyer", author_email: "buyer@example.test", created_at: NOW }); return send(state.notes.at(-1), 201); }
      return send(state.notes);
    }
    if (path === `/api/v1/documents/purchase_order/${poId}/attachments`) return send([]);
    if (path === `${base}/attachments` && method === "GET") return send([{ id: "f1", display_name: "quote.pdf", document_id: poId, scan_status: "clean", uploaded_at: NOW, vendor_facing: state.marks.f1, version: 1 }]);
    if (path === `${base}/attachments/f1/vendor-facing`) { state.marks.f1 = (req.postDataJSON() as { vendor_facing: boolean }).vendor_facing; return send({ vendor_facing: state.marks.f1 }); }
    return send({ detail: "Not found." }, 404);
  });
  return state;
}
