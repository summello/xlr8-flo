import type { Page } from "@playwright/test";

export const poId = "11111111-1111-4111-8111-111111111111";
export const nextId = "99999999-9999-4999-8999-999999999999";
const unitId = "55555555-5555-4555-8555-555555555555";
const reqId = "33333333-3333-4333-8333-333333333333";
const vendorId = "44444444-4444-4444-8444-444444444444";
const projectId = "66666666-6666-4666-8666-666666666666";
const NOW = "2026-10-12T10:00:00Z";

export type Tx = Record<string, unknown> & { id: string; attempt: number; result: string };
/** One transmission row of PO `poId`; override any field. */
export const tx = (result: string, over: Record<string, unknown> = {}): Tx => ({ id: `t${over.attempt ?? 1}-${result}`, po_id: poId, revision: 1, version: 3, attempt: 1, recipient_name: "Dock", recipient_email: "dock@example.test", template_version: "po-issued-v1", provider_message_id: null, result, created_at: NOW, result_at: result === "queued" ? null : NOW, result_reason: null, ...over });
export const contactIds = { dock: "c0000000-0000-4000-8000-000000000001", office: "c0000000-0000-4000-8000-000000000002" };

type Options = { transmissions?: Tx[]; resend?: "ok" | "403" | "409"; status?: string; budget?: "ok" | "forbidden" | "superseded"; vendorStatus?: string | null; vendorCopy?: "ok" | "unissued" | "404" | "500"; leaks?: boolean };

/** Internal values the vendor copy page must never show (EML-005): the stub serves them from the other PO routes only. */
export const LEAKS = ["PRJ-LEAK-9", "DEPT-LEAK-9", "LEDG-LEAK-9", "777777.7700", "777,777.77", "INTERNAL-NOTE-LEAK-9"];

export const counts = { all: 12, draft: 1, awaiting: 2, ready: 3, issued: 4, attention: 2 };

const line = (n: number, leaks: boolean) => ({ line_no: n, rfq_line_id: `r${n}`, requisition_line_id: `q${n}`, description: `Pump ${n}`, uom_code: "EA", quantity: "4.0000", unit_price: "100.0000", project_id: projectId, department_code: leaks ? "DEPT-LEAK-9" : "OPS", ledger_account_code: leaks ? "LEDG-LEAK-9" : "6100", price: "400.0000", tax: "40.0000", freight: "10.0000", line_total: "450.0000", budget_amount: "450.0000", budget_amount_project: "450.0000", value: "450.0000", value_converted: "450.0000" });

/** An in-memory stand-in for the PO API. Every budget figure is a deliberately odd number: the page must show the server's, never its own sum. */
export async function stubPo(page: Page, { status = "issued", budget = "ok", vendorStatus = "active", vendorCopy = "ok", leaks = false, transmissions, resend = "ok" }: Options = {}) {
  const base = `/api/v1/purchase-orders`;
  const state = { queries: [] as URL[], paths: [] as string[], copyFails: vendorCopy === "500", rows: transmissions ?? [tx("delivered")], resends: [] as { headers: Record<string, string>; body: unknown }[], writes: [] as string[] };
  const po = () => ({
    id: poId, number: "PO-0007", unit_id: unitId, vendor_id: vendorId, vendor_snapshot: { trading_name: "Acme Tooling" }, requisition_id: reqId, award_id: "a1", currency: "USD", converted_currency: "USD",
    rate: { rate: "1", to: "USD" }, bill_to: { line1: "1 Main St", city: "Springfield", country: "US" }, ship_to_address_id: null, recipients: [{ name: "Dock", email: "dock@example.test" }], requester_id: unitId,
    terms: "Net 30", needed_by: null, status, version: 3, revision: 1, supersedes_id: null, created_at: NOW, freight_mode: "line", document_freight: null,
    lines: Array.from({ length: 40 }, (_, i) => line(i + 1, leaks)), price_total: "16000.0000", tax_total: "1600.0000", freight_total: "400.0000", total: "18000.0000", budget_total: "18000.0000", budget_total_project: "18000.0000", total_converted: "18000.0000",
    released_amount: null, released_currency: null, vendor_status: vendorStatus, actions: [],
  });
  const listRow = (id: string, number: string, over: Record<string, unknown>) => ({ id, number, unit_id: unitId, vendor_id: vendorId, vendor_name: "Acme Tooling", requisition_id: reqId, currency: "USD", total: "18000.0000", status: "issued", created_at: NOW, revision: 1, projects: [{ id: projectId, code: "P-100", name: "Plant 4" }], project_count: 1, sent: { result: "delivered", result_at: NOW }, ...over });
  const copyRead = (id: string, revision: number, issued: boolean) => ({
    issued, template_version: "po-v1", email_summary: "Purchase order PO-0007 from Acme Buyer.\nTotal: 18000.0000 USD",
    vendor_copy: { number: "PO-0007", revision, issued_on: "2026-10-12", needed_by: null, currency: "USD", vendor_trading_name: "Acme Tooling", vendor_legal_name: "Acme Tooling Ltd", bill_to: { line1: "1 Main St", city: "Springfield", country: "US" }, ship_to: null, terms: "Net 30", letterhead_address: "Buyer Co, 9 High St", standard_terms: "Standard terms apply.",
      lines: [{ line_no: 1, description: "Pump 1", quantity: "4.0000", uom_code: "EA", unit_price: "100.0000", price: "400.0000", tax: "40.0000", freight: "10.0000", line_total: "450.0000" }],
      price_total: "400.0000", tax_total: "40.0000", freight_total: "10.0000", total: "450.0000", notes: [{ body: "Deliver to gate 4.", created_on: "2026-10-12" }] },
  });
  await page.route("**/api/v1/**", async route => {
    const req = route.request(), url = new URL(req.url()), path = url.pathname;
    state.paths.push(path);
    const send = (data: unknown, code = 200) => route.fulfill({ status: code, contentType: "application/json", body: JSON.stringify(data) });
    if (path === "/api/v1/auth/sessions" || path === "/api/v1/master/currency") return send([]);
    if (path === "/api/v1/auth/me") return send({ identity_id: unitId });
    if (path === "/api/v1/auth/organizations") return send({ items: [], current_org_id: unitId });
    if (path === base) {
      state.queries.push(url);
      const tab = url.searchParams.get("tab") ?? "all";
      const q = url.searchParams.get("q");
      const rows = [
        listRow(poId, "PO-0007", {}),
        listRow("a0000000-0000-4000-8000-000000000002", "PO-0008", { revision: 0, projects: [{ id: projectId, code: "P-100", name: "Plant 4" }, { id: "b", code: "P-200", name: "Line 2" }], project_count: 2, sent: { result: "bounced", result_at: NOW } }),
      ];
      return send({ rows: tab === "draft" || q === "none" ? [] : rows, next_cursor: null, counts });
    }
    if (path === `${base}/${poId}/vendor-copy` || path === `${base}/${nextId}/vendor-copy`) {
      if (vendorCopy === "404") return send({ detail: "Not found." }, 404);
      if (state.copyFails) { return send({ title: "Error", status: 500, detail: "Boom.", correlation_id: "c" }, 500); }
      return send(path.includes(nextId) ? copyRead(nextId, 0, true) : copyRead(poId, 1, vendorCopy !== "unissued"));
    }
    if (path === `${base}/${poId}`) return send(po());
    if (path === `${base}/${poId}/versions`) return send([{ revision: 0, po_id: nextId, status: "superseded", total: "17000.0000", currency: "USD", issued_at: NOW, is_current: false }, { revision: 1, po_id: poId, status, total: "18000.0000", currency: "USD", issued_at: NOW, is_current: true }]);
    if (path === `${base}/${poId}/changes`) return send([{ id: "c1", po_id: poId, number: 1, version: 1, from_version: 1, status: "approved", material: true, reason: "Price rise", created_by: unitId, created_at: NOW, draft: {}, diff: [], budget_effect: [], result_po_id: poId, total: "18000.0000", budget_total_project: "18000.0000" }]);
    if (path === `${base}/${poId}/history`) return send({ entries: [{ occurred_at: NOW, actor: "buyer@example.test", action: "purchase_order.issue", detail: "Status: issued" }], truncated: false });
    if (req.method() !== "GET") state.writes.push(`${req.method()} ${path}`);
    if (path === `${base}/${poId}/transmissions/resend` && req.method() === "POST") {
      state.resends.push({ headers: req.headers(), body: req.postDataJSON() });
      if (resend === "403") return send({ title: "Forbidden", status: 403, detail: "You may not resend this purchase order.", correlation_id: "c" }, 403);
      if (resend === "409") return send({ title: "Conflict", status: 409, detail: "An email for this order is still being sent.", correlation_id: "c", checks: { problem: "transmission_queued" } }, 409);
      const ids = (req.postDataJSON() as { contact_ids?: string[] }).contact_ids;
      const who = ids ? ids.map(i => (i === contactIds.office ? ["Office", "office@example.test"] : ["Dock", "dock@example.test"])) : [["Dock", "dock@example.test"]];
      const attempt = Math.max(...state.rows.map(r => r.attempt)) + 1;
      state.rows = [...who.map(([n, e]) => tx("queued", { attempt, id: `t${attempt}-${n}`, recipient_name: n, recipient_email: e })), ...state.rows];
      return send(state.rows);
    }
    if (path === `/api/v1/vendors/${vendorId}/contacts`) return send([{ id: contactIds.dock, name: "Dock", email: "dock@example.test", type: "ordering", receives_documents: true, phone: null, suppressed: true, created_at: NOW, updated_at: NOW },
      { id: contactIds.office, name: "Office", email: "office@example.test", type: "accounts", receives_documents: false, phone: null, suppressed: false, created_at: NOW, updated_at: NOW }]);
    if (path === `${base}/${poId}/transmissions`) return send(state.rows);
    if (path === `${base}/${poId}/budget-summary`) {
      if (budget === "forbidden") return send({ title: "Forbidden", status: 403, detail: "No.", correlation_id: "c" }, 403);
      if (budget === "superseded") return send({ title: "Conflict", status: 409, detail: "Replaced.", correlation_id: "c", checks: { problem: "superseded", current_id: nextId } }, 409);
      return send({ projects: [{ project_id: projectId, project_code: leaks ? "PRJ-LEAK-9" : "P-100", project_name: "Plant 4", currency: "USD", committed: leaks ? "777777.7700" : "5000.0000", this_po: "18000.0000", this_po_state: "committed", available_after_issue: "1.0000", actual: "250.0000" }] });
    }
    if (path === `/api/v1/requisitions/${reqId}/links`) return send({ nodes: [], partial: false });
    if (path.startsWith("/api/v1/documents/purchase_order/") && path.endsWith("/notes") && leaks) return send([{ id: "n1", body: "INTERNAL-NOTE-LEAK-9", created_at: NOW, author_name: "Buyer" }]);
    if (path.startsWith("/api/v1/documents/purchase_order/")) return send([]);
    return send({ detail: "Not found." }, 404);
  });
  return state;
}
