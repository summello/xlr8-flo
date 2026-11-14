import type { Page } from "@playwright/test";

export const rfqId = "11111111-1111-4111-8111-111111111111";
const unitId = "55555555-5555-4555-8555-555555555555";
const reqId = "33333333-3333-4333-8333-333333333333";
const NOW = "2026-10-01T00:00:00Z";
const [A, B, C] = ["aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", "cccccccc-cccc-4ccc-8ccc-cccccccccccc"];
const [L1, L2] = ["l1", "l2"];
const conversion = (currency: string, rate: string) => ({ base: currency, quote: "USD", rate, source: "Central bank", effective_date: "2026-10-01", stale: false });

/** The canvas's worked example: three vendors, two lines. Bolt (EUR) is lowest on a full bid, Cirrus is fastest, partial and late, Acme scores highest. Every figure is the server's. */
export function comparison(over: Record<string, unknown> = {}) {
  const vendor = (id: string, name: string, patch: Record<string, unknown>) => ({ vendor_id: id, vendor_name: name, bid_id: `bid-${name}`, currency: "USD", sent_at: NOW, rfq_version: 1, on_current_version: true, not_quoted: false, full: true, freight_unallocated: null, freight_unallocated_converted: null, ready_by: "2026-12-01", exceptions_count: 0, late: null, conversion: conversion("USD", "1"), unmet: 0, met: 2, met_of: 2, score: null, ...patch });
  return {
    sealed: false, currency: "USD", comparison_date: "2026-10-27", rate_date: "2026-10-26", frozen_at: NOW, stale_rate: false, bids_since_freeze: 0, approved_scope: "200000.0000",
    vendors: [
      vendor(A, "Acme Tooling", { price: "90000.0000", tax: "9000.0000", freight: "4000.0000", landed_total: "103000.0000", difference_from_lowest: "6500.0000", lead_time_days: 14, meets_need: true, score: "82.5000", exceptions_count: 1 }),
      vendor(B, "Bolt Supply", { currency: "EUR", conversion: conversion("EUR", "1.0850"), price: "85000.0000", tax: "8500.0000", freight: "3000.0000", landed_total: "96500.0000", difference_from_lowest: "0.0000", lead_time_days: 21, meets_need: false, ready_by: "2026-12-20", met: 1, unmet: 1, score: "70.0000" }),
      vendor(C, "Cirrus Works", { full: false, price: "40000.0000", tax: "4000.0000", freight: "1000.0000", landed_total: "45000.0000", difference_from_lowest: null, lead_time_days: 7, meets_need: true, late: { accepted_by_name: "Lena Lead", accepted_by_email: "lead@example.test", at: NOW, reason: "Courier delay" } }),
    ],
    lines: [[L1, 1, "Centrifugal pump", "4.0000", ["60000.0000", "58000.0000", "40000.0000"]], [L2, 2, "Spare seal kit", "2.0000", ["43000.0000", "38500.0000", null]]].map(([id, no, description, quantity, landed], i) => ({
      rfq_line_id: id, line_no: no, description, quantity, needed_by: null, identical: false, lowest: { vendor_id: i === 0 ? C : B, tied: false },
      cells: [A, B, C].map((v, k) => ({ vendor_id: v, not_quoted: (landed as (string | null)[])[k] === null, unit_price: (landed as (string | null)[])[k] === null ? null : "10.0000", unit_price_converted: (landed as (string | null)[])[k] === null ? null : ["11111.1100", "12345.6700", "9999.9900"][k], quantity, price: null, tax: null, freight_share: null, landed: (landed as (string | null)[])[k] })),
    })),
    identical_lines: 0, identical_measures: ["exceptions_count"], identical_rows: 1,
    markers: { lowest: { vendor_id: B, tied: false }, fastest: { vendor_id: C, tied: false }, highest_score: { vendor_id: A, tied: false } },
    lowest_cost_supplier: { vendor_id: null, total: "93000.0000", partial: false, within_scope: true, over_by: null, to_spare: "107000.0000", vendors: [{ vendor_id: C, total: "40000.0000", lines: [L1] }, { vendor_id: B, total: "53000.0000", lines: [L2] }], not_lowest_lines: [L2], not_lowest_count: 1 },
    best_average_cost: { vendor_id: B, total: "96500.0000", partial: false, within_scope: true, over_by: null, to_spare: "103500.0000", vendors: [{ vendor_id: B, total: "96500.0000", lines: [L1, L2] }], not_lowest_lines: [], not_lowest_count: 0 },
    insight: { difference: "3500.0000", difference_pct: "3.6", dearer_basis: "best_average_cost", lowest_cost_supplier_vendors: 2, best_average_cost_vendors: 1 },
    ...over,
  };
}

export type Stub = { refreshes: { key?: string }[] };

/** An in-memory stand-in for the sourcing API: one closed RFQ and its comparison. */
export async function stubComparison(page: Page, { body = comparison(), bidsReceived = 3, canEdit = true, bidRefused = false, scored = true, status = "closed" }: { body?: unknown; bidsReceived?: number; canEdit?: boolean; bidRefused?: boolean; scored?: boolean; status?: string } = {}): Promise<Stub> {
  const state: Stub = { refreshes: [] };
  const detail = () => ({
    id: rfqId, number: "RFQ-0001", title: "Centrifugal pumps", status, unit_id: unitId, currency: "USD", owner_email: "buyer@example.test", version: 1, edit_revision: 1, created_at: NOW, updated_at: NOW, deadline_at: "2026-10-20T00:00:00Z", deadline_tz: "UTC", published_at: NOW,
    lines: [], invitees: [], can_edit: canEdit, can_approve: false, bids_received: bidsReceived, requirements: [{ id: "q1", position: 1, text: "ISO 9001 certified", waived: false, waived_by_email: null, waived_by_name: null, waived_reason: null }, { id: "q2", position: 2, text: "Two year warranty", waived: false, waived_by_email: null, waived_by_name: null, waived_reason: null }],
  });
  const base = `/api/v1/sourcing/rfqs/${rfqId}`;
  await page.route("**/api/v1/**", async route => {
    const req = route.request(), path = new URL(req.url()).pathname;
    const send = (data: unknown, code = 200) => route.fulfill({ status: code, contentType: "application/json", body: JSON.stringify(data) });
    if (path === "/api/v1/auth/sessions" || path === "/api/v1/master/currency") return send([]);
    if (path === "/api/v1/auth/me") return send({ identity_id: unitId });
    if (path === "/api/v1/auth/organizations") return send({ items: [], current_org_id: unitId });
    if (path === base) return send(detail());
    if (path === `${base}/versions` || path === `${base}/history`) return send([]);
    if (path === `${base}/comparison`) return send(body);
    if (path === `${base}/comparison/refresh-rate` && req.method() === "POST") { state.refreshes.push({ key: req.headers()["idempotency-key"] }); return send({ ...(body as object), stale_rate: false }); }
    if (path === `${base}/evaluation`) return send({ status: "closed", scoring_open: false, criteria: [{ id: "k1", position: 1, name: "Quality", weight: 60 }, { id: "k2", position: 2, name: "Service", weight: 40 }], source: "rfq", edit_revision: 1, me: null, evaluators: canEdit ? [{ identity_id: "e1", display_name: "Eve Evaluator", version: 1, declaration: "clear", detail: null, declared_at: NOW, submitted: true }] : null, evaluator_count: 1, complete: true, vendors: [], scores: scored ? [{ identity_id: "e1", vendor_id: A, criterion_id: "k1", score: 8, comment: null, status: "submitted" }, { identity_id: "e2", vendor_id: A, criterion_id: "k1", score: 9, comment: null, status: "submitted" }, { identity_id: "e1", vendor_id: B, criterion_id: "k2", score: 6, comment: null, status: "submitted" }] : [], weighted: scored ? [{ vendor_id: A, score: "82.5000" }, { vendor_id: B, score: "70.0000" }] : null });
    const one = new RegExp(`^${base}/bids/([^/]+)$`).exec(path);
    if (one) return bidRefused ? send({ detail: "Forbidden" }, 403) : send({ id: one[1], rfq_id: rfqId, vendor_id: B, rfq_version: 1, currency: "EUR", sent_at: NOW, evidence_attachment_id: null, keyed_by: unitId, keyed_at: NOW, freight: "3000", lead_time_days: 21, valid_until: "2027-01-31", delivery_terms: "DAP Kuwait", warranty: "2 years", exceptions: "Excludes crating", status: "submitted", submitted_at: NOW, replaces_bid_id: null, row_version: 1, lines: [], requirements: [{ requirement_id: "q1", met: true, note: null }, { requirement_id: "q2", met: false, note: "Certificate pending" }], replaced_by_bid_id: null, current: true, late: null });
    return send({ detail: "Not found." }, 404);
  });
  return state;
}
