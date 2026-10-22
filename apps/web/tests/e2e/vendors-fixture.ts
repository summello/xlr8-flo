import type { Page } from "@playwright/test";

export const vendorId = "11111111-1111-4111-8111-111111111111";
const second = "22222222-2222-4222-8222-222222222222";
const unitId = "33333333-3333-4333-8333-333333333333";
const attachmentId = "44444444-4444-4444-8444-444444444444";
const docId = "55555555-5555-4555-8555-555555555555";

const NOW = "2026-10-01T00:00:00Z";
function vendor(id: string, n: number, name: string, documents: unknown[], patch: Record<string, unknown> = {}) {
  return {
    id, number: `VEN-${String(n).padStart(4, "0")}`, trading_name: name, legal_name: `${name} Incorporated of a Very Long Registered Name`, status: "active", availability: "global",
    created_by_email: "dev@example.test", created_at: NOW, updated_at: NOW, version: 1, sensitive: { version: 0 }, documents,
    eligibility: { eligible: documents.length === 0, reasons: documents.length === 0 ? [] : [{ code: "document_expired", text: "Insurance Certificate expired 2 Oct" }], warnings: [] }, ...patch,
  };
}
export const rows = [
  vendor(vendorId, 1, "Acme Tooling", [{ state: "expiring", count: 1, days_left: 12 }, { state: "expired", count: 1 }]),
  vendor(second, 2, "Borealis Steel", []),
  vendor("66666666-6666-4666-8666-666666666666", 3, "Cobalt Freight", [{ state: "missing", count: 1 }]),
];

/** An in-memory stand-in for the vendor API. Every list request is recorded so a test can assert server paging. */
export async function stubVendors(page: Page) {
  const state = {
    requests: [] as URL[],
    verified: false,
    docs: [
      { type_code: "INS", required: true, state: "expiring", days_left: 12, document_id: docId, attachment_id: attachmentId, attachment_version: 2, scan_status: "clean", issue_date: "2025-10-01", expiry_date: "2026-10-28", verification: "unverified", created_at: NOW },
      { type_code: "TAX", required: true, state: "missing" },
    ] as Record<string, unknown>[],
  };
  await page.route("**/api/v1/**", async route => {
    const req = route.request(), url = new URL(req.url()), path = url.pathname, method = req.method();
    const send = (body: unknown, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
    if (path === "/api/v1/auth/sessions" || path === "/api/v1/master/currency") return send([]);
    if (path === "/api/v1/auth/organizations") return send({ items: [], current_org_id: unitId });
    if (path === "/api/v1/org/units") return send({ rows: [{ id: unitId, code: "P4", name: "Plant 4", kind: "bu", active: true, parent_id: null, created_at: NOW }], next_cursor: null });
    if (path === "/api/v1/vendors/counts") return send({ active: 3, pending: 1, attention: 7, inactive: 0, all: 4 });
    if (path === "/api/v1/vendors") {
      state.requests.push(url);
      const q = (url.searchParams.get("q") ?? "").toLowerCase();
      const wanted = url.searchParams.getAll("documents");
      const all = rows.filter(r => (!q || r.trading_name.toLowerCase().includes(q)) && (!wanted.length || (r.documents as { state: string }[]).some(d => wanted.includes(d.state))));
      const limit = Number(url.searchParams.get("limit") ?? 50), start = Number(url.searchParams.get("cursor") ?? 0);
      const next = start + limit < all.length ? String(start + limit) : null;
      return send({ rows: all.slice(start, start + limit), next_cursor: next });
    }
    if (path === `/api/v1/vendors/${vendorId}`) return send({ ...rows[0], units: [], disabled_in: [], identifiers: {} });
    if (path === `/api/v1/vendors/${vendorId}/eligibility`) return send(rows[0]!.eligibility);
    if (path === `/api/v1/vendors/${vendorId}/contacts`) return send([{ id: second, type: "billing", name: "Pat Payer", email: "pat@acme.test", phone: null, receives_documents: false, created_at: NOW, updated_at: NOW, version: 1 }]);
    if (path === `/api/v1/vendors/${vendorId}/documents`) return send(state.docs);
    if (path === `/api/v1/vendors/${vendorId}/changes`) return send([]);
    if (path === `/api/v1/vendors/${vendorId}/onboarding`) return send({}, 403);
    if (path === `/api/v1/documents/vendor/${vendorId}/attachments`) return send([{ id: attachmentId, display_name: "insurance.pdf", document_id: vendorId, document_type: "vendor", media_type: "application/pdf", original_filename: "insurance.pdf", scan_status: "clean", sha256: "a", size_bytes: 2048, uploaded_at: NOW, uploaded_by: second, uploaded_by_email: "dev@example.test", version: 2, versions: 2, deleted_at: null }]);
    if (path === `/api/v1/vendors/${vendorId}/documents/${docId}/verify` && method === "POST") {
      state.verified = true;
      state.docs[0] = { ...state.docs[0], verification: "verified", reviewed_at: NOW };
      return send(state.docs[0]);
    }
    return send({ detail: "Not found." }, 404);
  });
  return state;
}
