import type { Page } from "@playwright/test";
import { poId, stubPo } from "./po-screens-fixture";

const NOW = "2026-10-12T10:00:00Z";
const projectId = "66666666-6666-4666-8666-666666666666";
const vendorId = "44444444-4444-4444-8444-444444444444";
const unit = "55555555-5555-4555-8555-555555555555";
export const contactIds = { dock: "c0000000-0000-4000-8000-000000000001", office: "c0000000-0000-4000-8000-000000000002" };

type Result = "queued" | "sent" | "delivered" | "bounced" | "failed" | "complained" | "address_suppressed";
type Options = {
  /** `null` hides the vendor from the viewer (no vendor.view). */
  vendorStatus?: string | null; budget?: "ok" | "forbidden"; stale?: boolean; abortFirst?: boolean;
  recipients?: string[];
  /** One entry per GET of the transmissions list after issue: the result of each recipient, in order. The last repeats. */
  sequence?: Result[][];
  /** Rows of another revision or template, always returned. */
  noise?: boolean;
  /** The vendor contacts call: a list, an empty list, or a 500. */
  contacts?: "ok" | "empty" | "error";
};
export type Sent = { method: string; path: string; headers: Record<string, string>; body: unknown };

const NAMES: Record<string, string> = { Dock: "dock@example.test", Office: "office@example.test" };

/** `stubPo` for an approved PO, plus the routes of the issue moment, registered later so they win. Unmatched calls still 404. */
export async function stubIssue(page: Page, { vendorStatus = "active", budget = "ok", stale = false, abortFirst = false, recipients = ["Dock"], sequence = [["queued"]], noise = false, contacts = "ok" }: Options = {}) {
  await stubPo(page, { status: "approved", budget, vendorStatus });
  const state = { status: "approved", issuePosts: [] as Sent[], resends: [] as Sent[], transmissionGets: 0, contactGets: 0, attempt: 1, results: sequence, aborted: false };
  const base = `/api/v1/purchase-orders/${poId}`;
  const line = { line_no: 1, rfq_line_id: "r1", requisition_line_id: "q1", description: "Pump 1", uom_code: "EA", quantity: "4.0000", unit_price: "100.0000", project_id: projectId, department_code: "OPS", ledger_account_code: "6100", price: "400.0000", tax: "0.0000", freight: "0.0000", line_total: "400.0000", budget_amount: "400.0000", budget_amount_project: "400.0000", value: "400.0000", value_converted: "400.0000" };
  const detail = () => ({
    id: poId, number: "PO-0007", unit_id: unit, vendor_id: vendorId, vendor_snapshot: { trading_name: "Acme Tooling" }, requisition_id: "33333333-3333-4333-8333-333333333333", award_id: "a1", currency: "USD", converted_currency: "USD",
    rate: { rate: "1", to: "USD" }, bill_to: { line1: "1 Main St", city: "Springfield", country: "US" }, ship_to_address_id: null, recipients: recipients.map(name => ({ name, email: NAMES[name] })), requester_id: unit,
    terms: "Net 30", needed_by: null, status: state.status, version: 3, revision: 1, supersedes_id: null, created_at: NOW, freight_mode: "line", document_freight: null,
    lines: [line], price_total: "400.0000", tax_total: "0.0000", freight_total: "0.0000", total: "400.0000", budget_total: "400.0000", budget_total_project: "400.0000", total_converted: "400.0000",
    released_amount: null, released_currency: null, vendor_status: vendorStatus, actions: state.status === "approved" ? ["issue", "cancel"] : [],
  });
  const row = (n: number, result: Result, attempt: number, over: Record<string, unknown> = {}) => {
    const name = recipients[n]!;
    return { id: `t${attempt}-${n}`, po_id: poId, revision: 1, version: 3, attempt, recipient_name: name, recipient_email: NAMES[name], template_version: "po-issued-v1", provider_message_id: null, result, created_at: NOW, result_at: result === "queued" ? null : NOW, ...over };
  };
  const rows = () => {
    const shown = state.results[Math.min(Math.max(state.transmissionGets - 1, 0), state.results.length - 1)]!;
    const live = shown.map((r, n) => row(n, r, state.attempt));
    return noise ? [...live,
      { ...row(0, "bounced", 9), revision: 0, po_id: "88888888-8888-4888-8888-888888888888" },
      row(0, "failed", 9, { template_version: "po-cancelled-v1" })] : live;
  };
  await page.route("**/api/v1/**", async route => {
    const req = route.request(), path = new URL(req.url()).pathname, method = req.method();
    const send = (data: unknown, code = 200) => route.fulfill({ status: code, contentType: "application/json", body: JSON.stringify(data) });
    const record = (): Sent => ({ method, path, headers: req.headers(), body: req.postData() ? req.postDataJSON() : null });
    if (method === "GET" && path === base) return send(detail());
    if (method === "GET" && path === `${base}/vendor-copy`) {
      return send({ issued: false, template_version: "po-v1", email_summary: "Purchase order PO-0007", vendor_copy: {
        number: "PO-0007", revision: 1, currency: "USD", bill_to: {}, ship_to: null, issued_on: null, letterhead_address: null, needed_by: null, standard_terms: null, terms: "Net 30",
        vendor_legal_name: "Acme Tooling Ltd", vendor_trading_name: "Acme Tooling", price_total: "400.0000", tax_total: "0.0000", freight_total: "0.0000", total: "400.0000",
        lines: [{ line_no: 1, description: "Pump 1", quantity: "4.0000", uom_code: "EA", unit_price: "100.0000", line_total: "400.0000", price: "400.0000", tax: "0.0000", freight: "0.0000" }],
        notes: [{ body: "Deliver to dock 4" }] } });
    }
    if (method === "POST" && path === `${base}/issue`) {
      const sent = record(); state.issuePosts.push(sent);
      if (abortFirst && !state.aborted) { state.aborted = true; return route.abort(); }
      if (stale) return send({ title: "Conflict", status: 409, detail: "Stale.", correlation_id: "c", checks: { problem: "stale_version" } }, 409);
      state.status = "issued";
      return send(detail());
    }
    if (method === "GET" && path === `${base}/transmissions`) {
      if (state.status !== "issued") return send([]);
      state.transmissionGets += 1;
      return send(rows());
    }
    if (method === "POST" && path === `${base}/transmissions/resend`) {
      state.resends.push(record()); state.attempt += 1; state.results = [["queued"]]; state.transmissionGets = 1;
      return send(rows());
    }
    if (method === "GET" && path === `/api/v1/vendors/${vendorId}/contacts`) {
      state.contactGets += 1;
      if (contacts === "empty") return send([]);
      if (contacts === "error") return send({ title: "Error", status: 500, detail: "Contacts are down.", correlation_id: "c" }, 500);
      return send([{ id: contactIds.dock, name: "Dock", email: "dock@example.test", type: "ordering", receives_documents: true, phone: null, created_at: NOW, updated_at: NOW },
        { id: contactIds.office, name: "Office", email: "office@example.test", type: "accounts", receives_documents: false, phone: null, created_at: NOW, updated_at: NOW }]);
    }
    return route.fallback();
  });
  return state;
}
