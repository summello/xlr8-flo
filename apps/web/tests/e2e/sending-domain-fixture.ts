import type { Page } from "@playwright/test";

const ID = (n: number) => `00000000-0000-4000-8000-${String(n).padStart(12, "0")}`;
type Sup = { id: string; email: string; reason: "bounced" | "complained"; created_at: string };
type Domain = { spf: string | null; dkim: string | null; detail: string; checked_at: string | null; check_pending: boolean; po_email_allowed: boolean };

export const failing: Domain = { spf: "pass", dkim: "fail", detail: "DKIM TXT k._domainkey: failed", checked_at: "2026-11-08T09:00:00Z", check_pending: false, po_email_allowed: false };
export const passing: Domain = { spf: "pass", dkim: "pass", detail: "", checked_at: "2026-11-08T09:05:00Z", check_pending: false, po_email_allowed: true };

/** In-memory stand-in for the E13-S21 API; the stub, not the browser, decides pass, fail and paging. */
export async function stubSendingDomain(page: Page, opts: { domain?: Domain; count?: number; recent?: boolean } = {}) {
  const state = {
    domain: opts.domain ?? failing,
    afterCheck: passing as Domain,
    recent: opts.recent ?? false,
    rows: Array.from({ length: opts.count ?? 3 }, (_, i): Sup => ({ id: ID(i + 1), email: `user${String(i).padStart(2, "0")}@x.com`, reason: i % 2 ? "complained" : "bounced", created_at: "2026-11-07T10:00:00Z" })),
    writes: [] as { method: string; path: string; body: unknown; headers: Record<string, string> }[],
    queries: [] as string[],
    reads: 0,
  };
  await page.route("**/api/v1/**", async route => {
    const req = route.request(), url = new URL(req.url()), path = url.pathname, method = req.method();
    const send = (body: unknown, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
    if (path === "/api/v1/auth/sessions" || path === "/api/v1/master/currency") return send([]);
    if (path === "/api/v1/auth/organizations") return send({ items: [], current_org_id: ID(900) });
    if (path === "/api/v1/admin/sending-domain" && method === "GET") {
      state.reads += 1;
      if (state.domain.check_pending && state.reads > 1) state.domain = state.afterCheck;
      return send(state.domain);
    }
    if (path === "/api/v1/admin/sending-domain/check") {
      state.writes.push({ method, path, body: null, headers: req.headers() });
      if (state.recent) return send({ detail: "The sending domain was checked less than a minute ago.", checks: { problem: "domain_check_recent" } }, 429);
      state.reads = 0; state.domain = { ...state.domain, check_pending: true };
      return send(state.domain, 202);
    }
    if (path === "/api/v1/admin/email-suppressions" && method === "GET") {
      state.queries.push(url.search);
      const q = url.searchParams.get("q"), cursor = url.searchParams.get("cursor");
      const all = state.rows.filter(r => !q || r.email.startsWith(q));
      const start = cursor ? Number(cursor) : 0;
      const slice = all.slice(start, start + 50);
      return send({ rows: slice, next_cursor: start + 50 < all.length ? String(start + 50) : null });
    }
    const m = path.match(/^\/api\/v1\/admin\/email-suppressions\/([^/]+)$/);
    if (m && method === "DELETE") {
      state.writes.push({ method, path, body: req.postDataJSON(), headers: req.headers() });
      state.rows = state.rows.filter(r => r.id !== m[1]);
      return route.fulfill({ status: 204 });
    }
    return send({ rows: [], next_cursor: null });
  });
  return state;
}
