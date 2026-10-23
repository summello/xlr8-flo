import type { Page } from "@playwright/test";

export const existingId = "77777777-7777-4777-8777-777777777777";
const vendorId = "88888888-8888-4888-8888-888888888888";
const unitId = "33333333-3333-4333-8333-333333333333";
const NOW = "2026-10-01T00:00:00Z";

type Mode = "ok" | "workflow" | "duplicate";
/** An in-memory stand-in for the owner-draft vendor API (E11-S14). The question carries a weight the real API never sends, so a test can prove the form never renders it. */
export async function stubRequest(page: Page, opts: { approver?: boolean; submit?: Mode; unitsFail?: boolean } = {}) {
  const state = {
    checks: [] as string[], submitBody: null as Record<string, unknown> | null, submitted: false, created: 0,
    vendor: null as null | { trading_name: string; legal_name: string; availability: string; version: number },
    unitsHealed: false, sensitiveBodies: [] as Record<string, any>[], contacts: [] as Record<string, any>[], sensitive: 0, answers: {} as Record<string, unknown>, onboarding: 1, mode: opts.submit ?? "ok",
  };
  const question = { key: "size", position: 1, prompt: "How many staff?", type: "number", required: true, choices: null, weight: 40, points: 40 };
  const detail = () => ({
    id: vendorId, number: "VEN-0100", ...state.vendor, status: state.submitted ? "pending_approval" : "draft", created_by_email: "dev@example.test", created_at: NOW, updated_at: NOW,
    sensitive: { version: state.sensitive, ...(state.sensitive ? { tax_id: "*****6789" } : {}) }, units: [], disabled_in: [], identifiers: {},
  });
  await page.route("**/api/v1/**", async route => {
    const req = route.request(), url = new URL(req.url()), path = url.pathname, method = req.method();
    const send = (body: unknown, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
    const body = () => req.postDataJSON() as Record<string, any>;
    if (path === "/api/v1/auth/sessions" || path === "/api/v1/master/currency") return send([]);
    if (path === "/api/v1/auth/organizations") return send({ items: [], current_org_id: unitId });
    if (path === "/api/v1/org/units" && opts.unitsFail && !state.unitsHealed) return send({ detail: "Forbidden.", correlation_id: "c" }, 403);
    if (path === "/api/v1/org/units") return send({ rows: [{ id: unitId, code: "P4", name: "Plant 4", kind: "bu", active: true, parent_id: null, created_at: NOW }], next_cursor: null });
    if (path === "/api/v1/master/vendor_identifier_type") return send({ rows: [{ id: unitId, kind: "vendor_identifier_type", code: "DUNS", name: "DUNS Number", active: true, created_at: NOW, updated_at: NOW }], next_cursor: null });
    if (path === "/api/v1/vendors/duplicate-check") {
      state.checks.push(url.search);
      const hit = url.searchParams.get("tax_id") === "99-999";
      return send({ can_override: Boolean(opts.approver), matches: hit ? [{ field: "tax_id", match: true, vendor_id: existingId, vendor_number: "VEN-0009", trading_name: "Old Co" }] : [] });
    }
    if (path === "/api/v1/vendors/onboarding-requests" && method === "POST") {
      const b = body(); state.created++;
      state.vendor = { trading_name: b.trading_name, legal_name: b.legal_name, availability: b.availability, version: 1 };
      return send({ vendor_id: vendorId, vendor_number: "VEN-0100", vendor_status: "draft", version_number: 1, questions: [question], answers: {}, row_version: 1 }, 201);
    }
    if (path === `/api/v1/vendors/${existingId}`) return send({ detail: "Not found." }, 404);
    if (path === `/api/v1/vendors/${vendorId}` && method === "PATCH") { Object.assign(state.vendor!, body(), { version: state.vendor!.version + 1 }); return send(detail()); }
    if (path === `/api/v1/vendors/${vendorId}`) return state.vendor ? send(detail()) : send({ detail: "Not found." }, 404);
    if (path === `/api/v1/vendors/${vendorId}/contacts` && method === "POST") {
      const row = { id: `c${state.contacts.length}`, phone: null, created_at: NOW, updated_at: NOW, version: 1, ...body(), receives_documents: body().receives_documents };
      state.contacts.push(row); return send(row, 201);
    }
    if (path === `/api/v1/vendors/${vendorId}/contacts`) return send(state.contacts);
    if (path === `/api/v1/vendors/${vendorId}/sensitive` && method === "PUT") { state.sensitiveBodies.push(body()); state.sensitive++; return send({ changed: ["tax_id"], version: state.sensitive }); }
    if (path === `/api/v1/vendors/${vendorId}/onboarding/answers` && method === "PUT") {
      const a = body().answers as Record<string, string>;
      if (a.size !== undefined && !/^\d+$/.test(a.size)) return send({ detail: "Some answers need attention.", errors: [{ field: "answers.size", message: "Enter a number." }], checks: { problem: "invalid_answers" }, correlation_id: "c" }, 422);
      state.answers = a; state.onboarding++; return send({ vendor_id: vendorId, vendor_number: "VEN-0100", vendor_status: "draft", version_number: 1, questions: [question], answers: a, row_version: state.onboarding });
    }
    if (path === `/api/v1/vendors/${vendorId}/onboarding`) return send({ vendor_id: vendorId, vendor_number: "VEN-0100", vendor_status: "draft", version_number: 1, questions: [question], answers: state.answers, row_version: state.onboarding });
    if (path === `/api/v1/vendors/${vendorId}/documents`) return send([{ type_code: "INS", required: true, state: "missing" }]);
    if (path === `/api/v1/vendors/${vendorId}/submit` && method === "POST") {
      state.submitBody = body();
      if (state.mode === "workflow") return send({ detail: "No approval workflow is published for vendors.", checks: { problem: "workflow_not_set_up" }, correlation_id: "c" }, 409);
      if (state.mode === "duplicate" && !(opts.approver && body().override_reason)) return send({ detail: "Another vendor already uses this identifier.", errors: [{ field: "tax_id", message: "Already used by another vendor." }], checks: { problem: "duplicate_identifier" }, correlation_id: "c" }, 409);
      state.submitted = true;
      return send({ id: "ch1", vendor_id: vendorId, kind: "create", payload: {}, reason: null, status: "pending", requested_by_email: "dev@example.test", requested_at: NOW, decided_at: null });
    }
    return send({ detail: "Not found." }, 404);
  });
  return state;
}
