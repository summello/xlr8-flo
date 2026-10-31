import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

const rfqId = "11111111-1111-4111-8111-111111111111";
const meId = "66666666-6666-4666-8666-666666666666";
const otherId = "77777777-7777-4777-8777-777777777777";
const vendorA = "44444444-4444-4444-8444-444444444444";
const vendorB = "88888888-8888-4888-8888-888888888888";
const crit1 = "c1000000-0000-4000-8000-000000000001";
const crit2 = "c1000000-0000-4000-8000-000000000002";
const unitId = "55555555-5555-4555-8555-555555555555";
const NOW = "2026-10-01T00:00:00Z";
const minute = (ms: number) => new Date(Math.floor((Date.now() + ms) / 60_000) * 60_000).toISOString();

type Options = { open?: boolean; notEvaluator?: boolean; declared?: "clear" | "conflict"; lateVendor?: boolean; everyone?: boolean };
type Cell = { vendor_id: string; criterion_id: string; score: number; comment: string | null; status: "draft" | "submitted" };

/** An in-memory stand-in for the evaluation API. It withholds vendors and scores until the evaluator has declared no conflict and the deadline has passed, as the server does. */
async function stubEvaluation(page: Page, { open = true, notEvaluator = false, declared, lateVendor = false, everyone = false }: Options = {}) {
  const state = { declaration: declared ?? null as "clear" | "conflict" | null, cells: [] as Cell[], version: 1, requests: [] as string[], writes: [] as { method: string; path: string; ifMatch?: string; body: unknown }[], late: lateVendor };
  if (lateVendor) state.cells.push(...[crit1, crit2].map(c => ({ vendor_id: vendorA, criterion_id: c, score: 7, comment: null, status: "submitted" as const })));
  const vendors = () => [{ vendor_id: vendorA, vendor_name: "Acme Tooling" }, ...(state.late ? [{ vendor_id: vendorB, vendor_name: "Beta Supply" }] : [])];
  const criteria = [{ id: crit1, position: 1, name: "Technical fit", weight: 60 }, { id: crit2, position: 2, name: "Delivery plan", weight: 40 }];
  const submitted = () => vendors().every(v => criteria.every(c => state.cells.some(x => x.vendor_id === v.vendor_id && x.criterion_id === c.id && x.status === "submitted")));
  const view = () => {
    const cleared = state.declaration === "clear";
    const other = everyone && submitted() ? vendors().flatMap(v => criteria.map(c => ({ identity_id: otherId, vendor_id: v.vendor_id, criterion_id: c.id, score: 5, comment: "Solid", status: "submitted" }))) : [];
    return {
      status: open ? "under_evaluation" : "published", scoring_open: open && cleared, criteria, source: "rfq", edit_revision: 1,
      me: notEvaluator ? null : { identity_id: meId, version: state.version, declaration: state.declaration === null ? "none" : "clear", submitted: submitted() },
      evaluators: null, evaluator_count: 2, complete: other.length > 0, vendors: open && cleared ? vendors() : [],
      scores: open && cleared ? [...state.cells.map(c => ({ ...c, identity_id: meId })), ...other] : [], weighted: other.length ? vendors().map(v => ({ vendor_id: v.vendor_id, score: "6.00" })) : null,
    };
  };
  const detail = () => ({ id: rfqId, number: "RFQ-0001", title: "Centrifugal pumps", status: open ? "under_evaluation" : "published", unit_id: unitId, currency: "USD", owner_email: "buyer@example.test", version: 1, edit_revision: 1, created_at: NOW, updated_at: NOW, deadline_at: open ? minute(-86_400_000) : minute(2 * 86_400_000), deadline_tz: "UTC", published_at: NOW, lines: [], invitees: [], can_edit: false, can_approve: false });
  await page.route("**/api/v1/**", async route => {
    const req = route.request(), path = new URL(req.url()).pathname, method = req.method();
    state.requests.push(`${method} ${path}`);
    const send = (body: unknown, status = 200, headers: Record<string, string> = {}) => route.fulfill({ status, contentType: "application/json", headers, body: JSON.stringify(body) });
    const base = `/api/v1/sourcing/rfqs/${rfqId}`;
    if (path === "/api/v1/auth/sessions" || path === "/api/v1/master/currency") return send([]);
    if (path === "/api/v1/auth/me") return send({ identity_id: meId });
    if (path === "/api/v1/auth/organizations") return send({ items: [], current_org_id: unitId });
    if (path === base) return send(detail());
    if (path === `${base}/evaluation` && method === "GET") return state.declaration === "conflict" ? send({ title: "Conflict", status: 409, detail: "You declared a conflict and are recused from this RFQ.", checks: { problem: "recused" } }, 409) : send(view());
    const record = () => state.writes.push({ method, path: path.replace(base, ""), ifMatch: req.headers()["if-match"], body: req.postDataJSON() });
    if (path === `${base}/evaluation/declaration` && method === "POST") {
      record(); const body = req.postDataJSON();
      state.declaration = body.conflict ? "conflict" : "clear";
      return send({ identity_id: meId, display_name: "Me", version: 1, declaration: state.declaration, detail: body.detail ?? null, declared_at: NOW, submitted: false }, 201);
    }
    if (path === `${base}/evaluation/scores` && method === "PUT") {
      record();
      for (const c of req.postDataJSON().cells as Cell[]) { const held = state.cells.find(x => x.vendor_id === c.vendor_id && x.criterion_id === c.criterion_id); if (held) Object.assign(held, c); else state.cells.push({ ...c, comment: c.comment ?? null, status: "draft" }); }
      state.version += 1;
      return send({ version: state.version, saved: 1, submitted: false }, 200, { etag: `"${state.version}"` });
    }
    if (path === `${base}/evaluation/scores/submit` && method === "POST") {
      record(); state.cells.forEach(c => { c.status = "submitted"; }); state.version += 1;
      return send({ version: state.version, saved: state.cells.length, submitted: true });
    }
    return send({ detail: "Not found." }, 404);
  });
  return state;
}

const evaluation = `/sourcing/rfqs/${rfqId}/evaluation`;
const BID_CONTENT = /\/(bids|comparison)(\/|$)/;

test("declare no conflict, score, save for later, then submit", async ({ page }) => {
  const state = await stubEvaluation(page);
  await page.goto(evaluation);
  await expect(page.getByText("Do you have a conflict of interest with any invited vendor?")).toBeVisible();
  // before the declaration nothing of the bids is asked for or drawn
  expect(state.requests.filter(r => BID_CONTENT.test(r))).toEqual([]);
  await expect(page.getByRole("button", { name: "Submit Scores" })).toHaveCount(0);
  await expect(page.getByText("Acme Tooling", { exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "Save Declaration" }).click();
  await expect(page.getByRole("alert").filter({ hasText: "Answer yes or no." })).toBeVisible();
  await page.getByLabel("No", { exact: true }).check();
  await page.getByRole("button", { name: "Save Declaration" }).click();
  await expect(page.getByText("Acme Tooling", { exact: true })).toBeVisible();
  await expect(page.getByText("60%")).toBeVisible();
  // all or nothing: an empty cell stops the submit before anything is sent
  await page.getByLabel("Score for Technical fit, Acme Tooling, 0 to 10").fill("8");
  await page.getByRole("button", { name: "Submit Scores" }).click();
  await expect(page.getByRole("alert").filter({ hasText: "Check 1 score" })).toBeFocused();
  await expect(page.getByRole("alert").filter({ hasText: "Enter a score from 0 to 10." }).first()).toBeVisible();
  expect(state.writes.filter(w => w.path.includes("scores"))).toHaveLength(0);
  await page.getByLabel("Score for Delivery plan, Acme Tooling, 0 to 10").fill("11");
  await page.getByRole("button", { name: "Save for Later" }).click();
  await expect(page.getByText("Enter a whole number from 0 to 10.").first()).toBeVisible();
  await page.getByLabel("Score for Delivery plan, Acme Tooling, 0 to 10").fill("6");
  await page.getByLabel("Comment for Delivery plan, Acme Tooling").fill("Tight but possible");
  await page.getByRole("button", { name: "Save for Later" }).click();
  await expect(page.getByText("Scores saved.")).toBeVisible();
  await expect(page.getByLabel("Score for Delivery plan, Acme Tooling, 0 to 10")).toHaveValue("6");
  await page.getByRole("button", { name: "Submit Scores" }).click();
  await expect(page.getByText("Scores submitted.")).toBeVisible();
  await expect(page.getByRole("button", { name: "Submit Scores" })).toHaveCount(0);
  await expect(page.getByText("Tight but possible")).toBeVisible();
  const writes = state.writes.filter(w => w.path.includes("scores"));
  expect(writes.map(w => `${w.method} ${w.path}`)).toEqual(["PUT /evaluation/scores", "PUT /evaluation/scores", "POST /evaluation/scores/submit"]);
  expect(writes[0]!.ifMatch).toBe("1");
  expect(writes[1]!.ifMatch).toBe("2");
});

test("declare a conflict and see the recused state", async ({ page }) => {
  const state = await stubEvaluation(page);
  await page.goto(evaluation);
  await page.getByLabel("Yes", { exact: true }).check();
  await page.getByRole("button", { name: "Save Declaration" }).click();
  await expect(page.getByRole("alert").filter({ hasText: "Describe the conflict." })).toBeVisible();
  await page.getByLabel(/^Describe the conflict/).fill("My brother owns Acme");
  await page.getByRole("button", { name: "Save Declaration" }).click();
  await expect(page.getByText("You are recused from this RFQ. The RFQ owner can see your declaration.")).toBeVisible();
  await expect(page.getByRole("button", { name: /Submit Scores|Declare/ })).toHaveCount(0);
  expect(state.requests.filter(r => BID_CONTENT.test(r))).toEqual([]);
});

test("before the deadline scoring is sealed: no vendor, figure or field", async ({ page }) => {
  await stubEvaluation(page, { open: false, declared: "clear" });
  await page.goto(evaluation);
  await expect(page.getByText(/Scoring Opens at the Deadline: /)).toBeVisible();
  await expect(page.getByRole("table")).toHaveCount(0);
  await expect(page.getByRole("textbox", { name: /Score for/ })).toHaveCount(0);
  await expect(page.getByText("Acme Tooling", { exact: true })).toHaveCount(0);
  await expect(page.locator(".eval-page").getByText(/\b\d+%/)).toHaveCount(0);
});

test("someone who declared no conflict can still declare one", async ({ page }) => {
  await stubEvaluation(page, { declared: "clear" });
  await page.goto(evaluation);
  await expect(page.getByText("Acme Tooling", { exact: true })).toBeVisible();
  await page.getByLabel(/^Describe the conflict/).fill("I own shares in Acme");
  await page.getByRole("button", { name: "Declare Conflict" }).click();
  await expect(page.getByText("You are recused from this RFQ.")).toBeVisible();
});

test("a late vendor brings new empty cells; submitted cells stay read-only", async ({ page }) => {
  await stubEvaluation(page, { declared: "clear", lateVendor: true });
  await page.goto(evaluation);
  await expect(page.locator("#page-title")).toBeVisible();
  await expect(page.getByLabel("Score for Technical fit, Acme Tooling, 0 to 10")).toHaveCount(0);
  await expect(page.getByLabel("Score for Technical fit, Beta Supply, 0 to 10")).toBeVisible();
  await expect(page.getByRole("button", { name: "Submit Scores" })).toBeVisible();
});

test("everyone's scores appear once the server says so", async ({ page }) => {
  await stubEvaluation(page, { declared: "clear", everyone: true });
  await page.goto(evaluation);
  await page.getByLabel("Score for Technical fit, Acme Tooling, 0 to 10").fill("9");
  await page.getByLabel("Score for Delivery plan, Acme Tooling, 0 to 10").fill("8");
  await page.getByRole("button", { name: "Submit Scores" }).click();
  await expect(page.getByRole("heading", { name: "Everyone's Scores" })).toBeVisible();
  await expect(page.getByRole("columnheader", { name: "Evaluator 2" })).toBeVisible();
  await expect(page.getByText("weighted score 6.00")).toBeVisible();
});

test("not an evaluator: one line and nothing else", async ({ page }) => {
  await stubEvaluation(page, { notEvaluator: true });
  await page.goto(evaluation);
  await expect(page.getByText("You are not an evaluator on this RFQ")).toBeVisible();
  await expect(page.getByRole("button", { name: /Declare|Submit|Save/ })).toHaveCount(0);
});

test("keyboard only: declare, score and submit", async ({ page }) => {
  await stubEvaluation(page);
  await page.goto(evaluation);
  await page.getByLabel("No", { exact: true }).focus();
  await page.keyboard.press("Space");
  await page.keyboard.press("Tab"); // the radio group is one stop; next is Save Declaration
  await expect(page.getByRole("button", { name: "Save Declaration" })).toBeFocused();
  await page.keyboard.press("Enter");
  const first = page.getByLabel("Score for Technical fit, Acme Tooling, 0 to 10");
  await first.focus();
  await page.keyboard.type("8");
  await page.keyboard.press("Tab"); // its comment
  await page.keyboard.press("Tab");
  await expect(page.getByLabel("Score for Delivery plan, Acme Tooling, 0 to 10")).toBeFocused();
  await page.keyboard.type("7");
  await page.getByRole("button", { name: "Submit Scores" }).focus();
  await page.keyboard.press("Enter");
  await expect(page.getByText("Scores submitted.")).toBeVisible();
});

for (const theme of ["light", "dark"] as const) {
  for (const stage of ["declaration", "scoring", "recused", "sealed"] as const) {
    test(`${stage} passes axe in ${theme} theme`, async ({ page }) => {
      await page.addInitScript(t => localStorage.setItem("xlr8flo.theme", t), theme);
      await stubEvaluation(page, stage === "declaration" ? {} : stage === "scoring" ? { declared: "clear" } : stage === "recused" ? { declared: "conflict" } : { open: false, declared: "clear" });
      await page.goto(evaluation);
      await page.locator(".app-shell").waitFor();
      await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
      await expect(page.locator("#page-title")).toBeVisible();
      await expect(page.locator(".eval-page").getByText(/^(Do you have|Acme Tooling|You are recused|Scoring Opens)/).first()).toBeVisible();
      expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
    });
  }
}
