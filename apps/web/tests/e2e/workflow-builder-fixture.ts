import type { Page } from "@playwright/test";
import type { components } from "../../src/api/generated/schema";
export const workflowId = "11111111-1111-4111-8111-111111111111";
export const approverId = "22222222-2222-4222-8222-222222222222";
export async function stubBuilder(page: Page) {
    const state = { graph: { nodes: [{ id: "start", kind: "start", name: "Start" }, { id: "end", kind: "end", name: "End" }], edges: [], layout: { start: { x: 40, y: 40 }, end: { x: 320, y: 40 } } } as components["schemas"]["WorkflowGraph"], report: { errors: [], warnings: [] } as components["schemas"]["ValidationReport"], catalog: { amount: "money" } as Record<string, string>, stale: false, failDelete: false, peopleFail: false, published: false, draft: true, writes: [] as {
            method: string;
            path: string;
            body: Record<string, unknown>;
            headers: Record<string, string>;
        }[] };
    const version = () => ({ id: workflowId, definition_id: workflowId, version: state.draft ? 1 : 2, status: state.published ? "published" : "draft", created_at: "2026-10-12T00:00:00Z", published_at: null, row_version: 1, created_by: approverId, published_by: null, graph: state.graph });
    await page.route("**/api/v1/**", async (route) => {
        const req = route.request(), u = new URL(req.url()), path = u.pathname;
        let body: unknown = { rows: [], next_cursor: null }, status = 200;
        if (req.method() !== "GET")
            state.writes.push({ method: req.method(), path, body: req.postDataJSON() ?? {}, headers: req.headers() });
        if (path === "/api/v1/auth/sessions" || path === "/api/v1/master/currency")
            body = [];
        else if (path === "/api/v1/auth/organizations")
            body = { items: [], current_org_id: workflowId };
        else if (path === "/api/v1/workflows/catalog")
            body = state.catalog;
        else if (path === "/api/v1/approvals/eligible-approvers") {
            if (state.peopleFail) {
                status = 503;
                body = { detail: "Approver service is unavailable." };
            }
            else
                body = { rows: [{ user_id: approverId, email: "approver@example.test", name: "Ada Lovelace" }, { user_id: "cccccccc-0000-4000-8000-000000000009", email: "unnamed@example.test", name: null }] };
        }
        else if (path === "/api/v1/org/units")
            body = { rows: [{ id: approverId }], next_cursor: null };
        else if (path.includes("/settings/"))
            body = { value: 17, source: { scope: "org" } };
        else if (path.endsWith("/validation"))
            body = state.report;
        else if (path.endsWith("/publish")) {
            state.published = true;
            body = version();
        }
        else if (path.endsWith("/versions") && req.method() === "POST") {
            state.graph = req.postDataJSON().graph;
            body = version();
        }
        else if (path.includes("/versions/")) {
            if (req.method() === "DELETE")
                return route.fulfill({ status: state.failDelete ? 409 : 204, ...(state.failDelete ? { contentType: "application/json", body: JSON.stringify({ detail: "Only a draft can be deleted.", checks: { problem: "not_draft" } }) } : {}) });
            if (req.method() === "PUT") {
                if (state.stale) {
                    status = 409;
                    body = { detail: "Stale draft", checks: { problem: "stale_version" } };
                }
                else {
                    state.graph = req.postDataJSON().graph;
                    body = version();
                }
            }
            else
                body = version();
        }
        else if (path === `/api/v1/workflows/${workflowId}`)
            body = { id: workflowId, name: "Capital Review", document_type: "requisition", scope_type: "org", scope_id: workflowId, created_by: approverId, created_at: "2026-10-12T00:00:00Z", retired_at: null, versions: [{ ...version(), status: state.draft ? "draft" : "published" }] };
        await route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
    });
    return state;
}
