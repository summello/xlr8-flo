import type { Page } from "@playwright/test";

type Q = { key: string; position: number; prompt: string; type: string; required: boolean; weight: number; options: unknown; row_version: number };
type V = { id: string; number: number; status: "draft" | "published" | "retired"; threshold: number; row_version: number; published_at: string | null; questions: Q[] };

const ID = (n: number) => `00000000-0000-4000-8000-${String(n).padStart(12, "0")}`;

/** An in-memory stand-in for the E11-S05 API: the weight total is computed here, as the server does. */
export async function stubQuestionnaires(page: Page, seed: V[] = []) {
  const state = { versions: seed, forceTotal: null as number | null, writes: [] as { method: string; path: string; body: unknown; headers: Record<string, string> }[] };
  const total = (v: V) => state.forceTotal ?? v.questions.reduce((n, q) => n + q.weight, 0);
  const read = (v: V) => ({ id: v.id, number: v.number, status: v.status, threshold: v.threshold, row_version: v.row_version, published_at: v.published_at, question_count: v.questions.length, weight_total: total(v) });
  const detail = (v: V) => ({ ...read(v), questions: v.questions.map((q, i) => ({ ...q, position: i + 1 })) });
  await page.route("**/api/v1/**", async route => {
    const req = route.request(), path = new URL(req.url()).pathname, method = req.method();
    const send = (body: unknown, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
    if (path === "/api/v1/auth/sessions" || path === "/api/v1/master/currency") return send([]);
    if (path === "/api/v1/auth/organizations") return send({ items: [], current_org_id: ID(1) });
    const m = path.match(/^\/api\/v1\/admin\/vendor-questionnaires(?:\/([^/]+)(?:\/(publish|copy|questions)(?:\/([^/]+)(?:\/(move))?)?)?)?$/);
    if (!m) return send({ rows: [], next_cursor: null });
    if (method !== "GET") state.writes.push({ method, path, body: req.postDataJSON(), headers: req.headers() });
    const [, vid, sub, key, move] = m;
    const v = state.versions.find(x => x.id === vid);
    if (!vid) {
      if (method === "GET") return send(state.versions.map(read));
      const next: V = { id: ID(100 + state.versions.length), number: state.versions.length + 1, status: "draft", threshold: 70, row_version: 1, published_at: null, questions: [] };
      state.versions.push(next);
      return send(detail(next), 201);
    }
    if (!v) return send({ detail: "Not found." }, 404);
    if (!sub) {
      if (method === "PATCH") { v.threshold = req.postDataJSON().threshold; v.row_version += 1; }
      return send(detail(v));
    }
    if (sub === "publish") {
      const sum = total(v);
      if (sum !== 100) return send({ detail: `The weights add up to ${sum}. They must add up to exactly 100 before this version can be published.`, checks: { problem: "weights_total", total: String(sum) }, correlation_id: "c1" }, 409);
      state.versions.forEach(x => { if (x.status === "published") x.status = "retired"; });
      v.status = "published"; v.published_at = "2026-10-16T09:00:00Z"; v.row_version += 1;
      return send(detail(v));
    }
    if (sub === "copy") {
      const next: V = { ...v, id: ID(100 + state.versions.length), number: state.versions.length + 1, status: "draft", published_at: null, row_version: 1, questions: v.questions.map(q => ({ ...q })) };
      state.versions.push(next);
      return send(detail(next), 201);
    }
    if (!key) {
      const body = req.postDataJSON();
      const q: Q = { ...body, position: v.questions.length + 1, row_version: 1 };
      v.questions.push(q);
      return send(q, 201);
    }
    const i = v.questions.findIndex(q => q.key === key);
    if (move) {
      const j = req.postDataJSON().direction === "up" ? i - 1 : i + 1;
      if (j >= 0 && j < v.questions.length) [v.questions[i], v.questions[j]] = [v.questions[j]!, v.questions[i]!];
      return send(detail(v));
    }
    Object.assign(v.questions[i]!, req.postDataJSON(), { row_version: v.questions[i]!.row_version + 1 });
    return send(v.questions[i]);
  });
  return state;
}

export const publishedVersion = (): V => ({
  id: ID(2), number: 1, status: "published", threshold: 70, row_version: 3, published_at: "2026-10-01T09:00:00Z",
  questions: [{ key: "insured", position: 1, prompt: "Insurance held?", type: "yes_no", required: true, weight: 100, options: { yes: 41, no: 0 }, row_version: 1 },
    ...[["years", "Years trading", "number", null], ["profile", "Company profile", "text", null], ["since", "Trading since", "date", null], ["policy", "Policy document", "file", null],
      ["size", "Company size", "single_choice", [{ value: "s", label: "Small", points: 7 }, { value: "l", label: "Large", points: 9 }]],
      ["regions", "Regions served", "multi_choice", [{ value: "n", label: "North", points: 7 }, { value: "s", label: "South", points: 9 }]],
    ].map(([key, prompt, type, options], i) => ({ key: key as string, position: i + 2, prompt: prompt as string, type: type as string, required: false, weight: 0, options, row_version: 1 }))],
});
