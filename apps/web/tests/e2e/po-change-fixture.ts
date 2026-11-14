import type { Page } from "@playwright/test";

export const poId = "11111111-1111-4111-8111-111111111111";
export const priorId = "99999999-9999-4999-8999-999999999999";
export const resultId = "88888888-8888-4888-8888-888888888888";
const unitId = "55555555-5555-4555-8555-555555555555";
const reqId = "33333333-3333-4333-8333-333333333333";
const vendorId = "44444444-4444-4444-8444-444444444444";
const projectId = "66666666-6666-4666-8666-666666666666";
const NOW = "2026-10-12T10:00:00Z";

type Json = Record<string, unknown>;
export type Call = { method: string; path: string; headers: Record<string, string>; body: Json | null; at: number };
export type Options = {
  actions?: string[];
  /** The change as the server holds it at the start. */
  status?: string;
  /** The PO uses document freight (none set). */
  documentMode?: boolean;
  /** Fields laid over the change at the start (a diff, material, ...). */
  seed?: Json;
  /** Hold the next PATCH this long (ms) before answering. */
  patchDelay?: number;
  /** Answer the PATCH with this 422 field error instead of accepting it. */
  patchInvalid?: { field: string; message: string };
  /** Scripted answer for apply / submit. */
  apply?: { status: number; body: Json };
  submit?: { status: number; body: Json };
  /** Answer POST …/changes with this. */
  start?: { status: number; body: Json };
};

const line = (n: number) => ({ line_no: n, rfq_line_id: `r${n}`, requisition_line_id: `q${n}`, description: `Pump ${n}`, uom_code: "EA", quantity: "4.0000", unit_price: "100.0000", project_id: projectId, department_code: "OPS", ledger_account_code: "6100", price: "400.0000", tax: "40.0000", freight: "10.0000", line_total: "450.0000", budget_amount: "450.0000", budget_amount_project: "450.0000" });

/** An in-memory stand-in for the PO and change-order API. Figures are deliberately odd: the page must show the server's, never its own sum. */
export async function stubChange(page: Page, o: Options = {}) {
  const calls: Call[] = [];
  const change: Json = {
    id: "c0000000-0000-4000-8000-000000000001", po_id: poId, number: 1, version: 1, from_version: 1, status: o.status ?? "draft", material: false, reason: null,
    created_by: unitId, created_at: NOW, draft: {}, diff: [], budget_effect: [], result_po_id: null, total: "18000.0000", budget_total_project: "18000.0000", ...o.seed,
  };
  const po = (id: string, revision: number, status: string) => ({
    id, number: "PO-0007", unit_id: unitId, vendor_id: vendorId, vendor_snapshot: { trading_name: "Acme Tooling" }, requisition_id: reqId, award_id: "a1", currency: "USD", converted_currency: "USD",
    rate: { rate: "1", to: "USD" }, bill_to: { line1: "1 Main St" }, ship_to_address_id: null, recipients: [], requester_id: unitId, terms: "Net 30", needed_by: null, status, version: 3, revision, supersedes_id: null,
    created_at: NOW, freight_mode: o.documentMode ? "document" : "line", document_freight: null, lines: [line(1), line(2)], price_total: "800.0000", tax_total: "80.0000", freight_total: "20.0000", total: "900.0000", budget_total: "900.0000",
    budget_total_project: "900.0000", total_converted: "900.0000", released_amount: null, released_currency: null, vendor_status: "active", actions: id === poId ? (o.actions ?? ["change_order"]) : [],
  });
  /** The default server: a date or terms edit is non-material; a quantity edit is material with a -17.03 effect. */
  const accept = (body: Json) => {
    const lines = (body.lines as Json[] | undefined) ?? [];
    const diff: Json[] = [];
    if (body.needed_by) diff.push({ line_no: null, field: "needed_by", label: "Needed by", was: null, now: String(body.needed_by) });
    for (const l of lines) if (l.quantity !== undefined) diff.push({ line_no: l.line_no, field: "quantity", label: "Quantity", was: "4.0000", now: String(l.quantity) });
    const material = lines.length > 0;
    Object.assign(change, {
      version: (change.version as number) + 1, reason: body.reason ?? null, diff, material,
      draft: { ...(body.needed_by ? { needed_by: body.needed_by } : {}), ...(material ? { lines: Object.fromEntries(lines.map(l => [String(l.line_no), { quantity: l.quantity }])) } : {}) },
      budget_effect: material ? [{ project_id: projectId, department_code: "OPS", ledger_account_code: "6100", currency: "USD", delta: "-17.0300" }] : [],
      total: material ? "18017.0300" : "18000.0000",
    });
  };
  await page.route("**/api/v1/**", async route => {
    const req = route.request(), url = new URL(req.url()), path = url.pathname, method = req.method();
    const send = (data: unknown, code = 200, headers: Record<string, string> = {}) => route.fulfill({ status: code, contentType: "application/json", headers, body: JSON.stringify(data) });
    const body = (req.postDataJSON() as Json | null) ?? null;
    const base = "/api/v1/purchase-orders";
    const csrf = { "Set-Cookie": "flo_csrf=test-csrf; Path=/; SameSite=Lax" };
    if (path === "/api/v1/auth/me") return send({ identity_id: unitId }, 200, csrf);
    if (path === "/api/v1/auth/sessions") return send([], 200, csrf);
    if (path === "/api/v1/master/currency") return send([]);
    if (path === "/api/v1/auth/organizations") return send({ items: [], current_org_id: unitId }, 200, csrf);
    if (path === `${base}/${poId}` ) return send(po(poId, 1, "issued"));
    if (path === `${base}/${priorId}`) return send(po(priorId, 0, "superseded"));
    if (path === `${base}/${resultId}`) return send(po(resultId, 2, "issued"));
    if (path.startsWith(`${base}/`) && path.endsWith("/budget-summary")) return send({ projects: [{ project_id: projectId, project_code: "P-100", project_name: "Plant 4", currency: "USD", committed: "5000.0000", this_po: "900.0000", this_po_state: "committed", available_after_issue: "1.0000", actual: "250.0000" }] });
    if (path.endsWith("/versions")) return send([{ revision: 0, po_id: priorId, status: "superseded", total: "800.0000", currency: "USD", issued_at: NOW, is_current: false }, { revision: 1, po_id: poId, status: "issued", total: "900.0000", currency: "USD", issued_at: NOW, is_current: true }]);
    if (path.endsWith("/history")) return send({ entries: [], truncated: false });
    if (path.endsWith("/transmissions")) return send([]);
    if (path === `/api/v1/requisitions/${reqId}/links`) return send({ nodes: [], partial: false });
    if (path.startsWith("/api/v1/documents/purchase_order/")) return send([]);
    const changes = `${base}/${poId}/changes`;
    if (path === changes && method === "GET") return send([change]);
    if (path === changes && method === "POST") {
      calls.push({ method, path, headers: req.headers(), body, at: Date.now() });
      const s = o.start ?? { status: 201, body: change };
      return send(s.body, s.status);
    }
    if (path === `${changes}/1` && method === "GET") return send(change, 200, { ETag: `"${change.version}"` });
    if (path === `${changes}/1` && method === "PATCH") {
      calls.push({ method, path, headers: req.headers(), body, at: Date.now() });
      if (o.patchInvalid) return send({ title: "Unprocessable", status: 422, detail: o.patchInvalid.message, correlation_id: "c", errors: [{ field: o.patchInvalid.field, message: o.patchInvalid.message }], checks: { problem: "invalid" } }, 422);
      const delay = o.patchDelay; o.patchDelay = undefined;
      if (delay) await new Promise(r => setTimeout(r, delay));
      accept(body ?? {});
      return send(change);
    }
    for (const kind of ["apply", "submit"] as const) if (path === `${changes}/1/${kind}` && method === "POST") {
      calls.push({ method, path, headers: req.headers(), body, at: Date.now() });
      const s = o[kind] ?? (kind === "apply" ? { status: 200, body: { ...change, status: "approved", result_po_id: resultId } } : { status: 200, body: { ...change, status: "approval_pending" } });
      return send(s.body, s.status);
    }
    return send({ detail: "Not found." }, 404);
  });
  return { calls, change, patches: () => calls.filter(c => c.method === "PATCH") };
}
