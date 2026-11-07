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

export const TAX = "12-3456789", ACCOUNT = "000123454821";
const clear = { tax_id: TAX, tax_id_country: "US", bank_name: "Acme Operating", bank_account: ACCOUNT, bank_routing: "SORT-20-00-00", version: 1 };
const masked = { tax_id: "•••• 6789", tax_id_country: "US", bank_name: "•••• ting", bank_account: "•••• 4821", bank_routing: "•••• 0-00", version: 1 };

/** An in-memory stand-in for the vendor API. Every list request is recorded so a test can assert server paging. */
export async function stubVendors(page: Page, { canReveal = true, canEdit = canReveal }: { canReveal?: boolean; canEdit?: boolean } = {}) {
  const state = {
    requests: [] as URL[],
    verified: false,
    reveals: [] as unknown[],
    puts: [] as { body: Record<string, unknown>; ifMatch: string | undefined }[],
    docs: [
      { type_code: "INS", required: true, state: "expiring", days_left: 12, document_id: docId, attachment_id: attachmentId, attachment_version: 2, scan_status: "clean", issue_date: "2025-10-01", expiry_date: "2026-10-28", verification: "unverified", created_at: NOW },
      { type_code: "TAX", required: true, state: "missing" },
    ] as Record<string, unknown>[],
    scores: [] as Record<string, any>[],
    /** When set, the stub reports this overall figure whatever the rows say: the browser must show the server's number. */
    overall: null as string | null,
  };
  let seq = 0;
  const uid = () => `aaaaaaaa-0000-4000-8000-${String(++seq).padStart(12, "0")}`;
  const current = () => state.scores.filter(s => !s.superseded_by);
  const performance = () => {
    const rows = current(), cats = [...new Set(rows.map(s => s.category_code as string))].sort();
    const avg = (c: string) => { const r = rows.filter(s => s.category_code === c); return r.reduce((n, s) => n + s.score, 0) / r.length; };
    const overall = cats.length ? (cats.reduce((n, c) => n + avg(c), 0) / cats.length).toFixed(1) : null;
    return { categories: cats.map(c => ({ category_code: c, average: avg(c).toFixed(1), count: rows.filter(s => s.category_code === c).length })), overall: state.overall ?? overall, count: rows.length };
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
    if (path === `/api/v1/vendors/${vendorId}`) return send({ ...rows[0], sensitive: masked, sensitive_access: { can_reveal: canReveal, can_edit: canEdit, ask: "Vendor Manager or Budget/Finance User" }, units: [], disabled_in: [], identifiers: {} });
    if (path === `/api/v1/vendors/${vendorId}/sensitive` && method === "PUT") {
      const body = req.postDataJSON() as Record<string, unknown>;
      state.puts.push({ body, ifMatch: req.headers()["if-match"] });
      return send({ changed: Object.keys(body), version: 2 });
    }
    if (path === `/api/v1/vendors/${vendorId}/sensitive/reveal` && method === "POST") {
      const body = req.postDataJSON() as { reason_code?: string; note?: string | null };
      state.reveals.push(body);
      if (!canReveal) return send({ detail: "Forbidden." }, 403);
      if (body.note && /\d{4,}/.test(body.note.replace(/[\s-]/g, ""))) return send({ detail: "The request contains invalid values. Your submitted values were preserved.", errors: [{ field: "note", message: "Value error, Leave account and tax numbers out of the note." }] }, 422);
      return send(clear);
    }
    if (path === `/api/v1/vendors/${vendorId}/eligibility`) return send(rows[0]!.eligibility);
    if (path === `/api/v1/vendors/${vendorId}/contacts`) return send([{ id: second, type: "billing", name: "Pat Payer", email: "pat@acme.test", phone: null, receives_documents: false, created_at: NOW, updated_at: NOW, version: 1 }]);
    if (path === `/api/v1/vendors/${vendorId}/documents`) return send(state.docs);
    if (path === `/api/v1/vendors/${vendorId}/performance`) return send(performance());
    if (path === `/api/v1/vendors/${vendorId}/scores` && method === "GET") return send({ rows: [...state.scores].reverse(), next_cursor: null });
    if (path === `/api/v1/vendors/${vendorId}/scores` && method === "POST") {
      const b = req.postDataJSON();
      const row = { id: uid(), category_code: b.category, score: b.score, period: b.period, source_type: "po", source_ref: b.source_ref, rationale: b.rationale, scored_by_email: "dev@example.test", scored_at: NOW, supersedes: null, superseded_by: null, disputes: [] };
      state.scores.push(row);
      return send(row, 201);
    }
    const dispute = path.match(new RegExp(`^/api/v1/vendors/${vendorId}/scores/([^/]+)/disputes(?:/([^/]+)/resolve)?$`));
    if (dispute && method === "POST") {
      const score = state.scores.find(s => s.id === dispute[1]);
      if (!score) return send({ detail: "Not found." }, 404);
      const b = req.postDataJSON();
      if (!dispute[2]) {
        const d = { id: uid(), score_id: score.id, raised_by_email: "dev@example.test", raised_at: NOW, reason: b.reason, resolution: null, resolved_by_email: null, resolved_at: null, resolution_note: null };
        score.disputes.push(d);
        return send(d, 201);
      }
      const d = score.disputes.find((x: { id: string }) => x.id === dispute[2]);
      Object.assign(d, { resolution: b.resolution, resolved_by_email: "boss@example.test", resolved_at: NOW, resolution_note: b.note });
      if (b.resolution === "upheld") {
        const adj = { ...score, id: uid(), score: b.adjusted_score, rationale: b.adjusted_rationale, supersedes: score.id, superseded_by: null, disputes: [] };
        score.superseded_by = adj.id;
        state.scores.push(adj);
      }
      return send(d);
    }
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
