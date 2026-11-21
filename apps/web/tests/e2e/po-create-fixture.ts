import type { Page } from "@playwright/test";

export const awardId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
export const rfqId = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
export const [ACME, BOLT] = ["cccccccc-cccc-4ccc-8ccc-cccccccccccc", "dddddddd-dddd-4ddd-8ddd-dddddddddddd"];
const [REQ1, REQ2] = ["11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222"];
const unitId = "55555555-5555-4555-8555-555555555555";

type Options = {
  status?: string;
  canOverride?: boolean;
  boltEligible?: boolean;
  /** The first POST for Bolt Supply is refused with a new open figure for its line. */
  race?: { open: string };
  allOrdered?: boolean;
};
export type Post = { key: string | undefined; body: { vendor_id: string; lines: { rfq_line_id: string; quantity: string }[]; override_reason?: string } };

/** Acme: line 1 on REQ-1 and line 2 on REQ-2. Bolt: line 3 on REQ-1. Three (vendor, requisition) groups, 10 open each. */
const lines = [
  { rfq_line_id: "L1", line_no: 1, vendor_id: ACME, requisition_id: REQ1, requisition_number: "REQ-1", description: "Pump", requisition_line_id: "Q1" },
  { rfq_line_id: "L2", line_no: 2, vendor_id: ACME, requisition_id: REQ2, requisition_number: "REQ-2", description: "Valve", requisition_line_id: "Q2" },
  { rfq_line_id: "L3", line_no: 3, vendor_id: BOLT, requisition_id: REQ1, requisition_number: "REQ-1", description: "Gasket", requisition_line_id: "Q3" },
];

/** An in-memory stand-in for awarded-lines and the PO create. A repeated Idempotency-Key replays the first response. */
export async function stubPoCreate(page: Page, { status = "approved", canOverride = false, boltEligible = true, race, allOrdered = false }: Options = {}) {
  const open: Record<string, number> = Object.fromEntries(lines.map(l => [l.rfq_line_id, allOrdered ? 0 : 10]));
  const state = { posts: [] as Post[], gets: 0 };
  const replies = new Map<string, unknown>();
  let raced = false;
  const awarded = () => ({
    award_id: awardId, award_number: "AWD-0001", award_status: status, rfq_id: rfqId, can_override: canOverride,
    vendors: [
      { vendor_id: ACME, vendor_name: "Acme Tooling", eligible: true, reasons: [] },
      { vendor_id: BOLT, vendor_name: "Bolt Supply", eligible: boltEligible, reasons: boltEligible ? [] : ["Vendor suspended"] },
    ],
    lines: lines.map(l => ({ ...l, uom_code: "EA", currency: "USD", unit_price: "100.0000", awarded_qty: "10.0000", ordered_qty: (10 - open[l.rfq_line_id]!).toFixed(4), open_qty: open[l.rfq_line_id]!.toFixed(4), ordered_value: "0.0000" })),
  });
  const problem = (status: number, detail: string, checks: Record<string, string>) => ({ type: "about:blank", title: "Problem", status, detail, correlation_id: "corr-1", checks });
  await page.route("**/api/v1/**", async route => {
    const req = route.request(), path = new URL(req.url()).pathname, method = req.method();
    const send = (data: unknown, code = 200) => route.fulfill({ status: code, contentType: "application/json", body: JSON.stringify(data) });
    if (path === "/api/v1/auth/sessions" || path === "/api/v1/master/currency") return send([]);
    if (path === "/api/v1/auth/me") return send({ identity_id: unitId });
    if (path === "/api/v1/auth/organizations") return send({ items: [], current_org_id: unitId });
    if (path === "/api/v1/purchase-orders/awarded-lines" && method === "GET") { state.gets++; return send(awarded()); }
    if (path === "/api/v1/purchase-orders" && method === "POST") {
      const key = req.headers()["idempotency-key"], body = req.postDataJSON() as Post["body"];
      state.posts.push({ key, body });
      if (key && replies.has(key)) return send(replies.get(key), 201);
      if (body.vendor_id === BOLT && race && !raced) {
        raced = true; // the figure is only in the 409: the awarded-lines read model lags, so the typed value still passes the field check
        return send(problem(409, "Over the open quantity.", { problem: "over_open_quantity", "open.L3": `${race.open}.0000`, "requested.L3": body.lines[0]!.quantity }), 409);
      }
      const bolt = body.vendor_id === BOLT;
      if (bolt && !boltEligible && !body.override_reason) return send(problem(409, "Vendor is not eligible.", { problem: "vendor_not_eligible", reasons: "Vendor suspended" }), 409);
      for (const l of body.lines) open[l.rfq_line_id] = open[l.rfq_line_id]! - Number(l.quantity);
      const reply = { id: `po-${state.posts.length}`, number: `PO-000${state.posts.length}` };
      if (key) replies.set(key, reply);
      return send(reply, 201);
    }
    return send({ detail: "Not found." }, 404);
  });
  return state;
}
