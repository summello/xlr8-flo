import type { Page } from "@playwright/test";

import type { components } from "../../src/api/generated/schema";
import { stubProjects } from "./projects-fixture";

type Row = components["schemas"]["InboxRow"];
const uuid = (n: number) => `aaaaaaaa-0000-4000-8000-${String(n).padStart(12, "0")}`;
const requester = "requester@example.test";

export function inboxRow(n: number, patch: Partial<Row> = {}): Row {
  return {
    task_id: uuid(n), instance_id: uuid(100 + n), document_type: "requisition", document_id: uuid(200 + n),
    number: `REQ-${String(400 + n).padStart(5, "0")}`, title: `Press brake ${n}`, requester_note: n === 1 ? "<b>Needed</b> by Friday" : null,
    requester_email: requester, requester_name: n === 1 ? "Ada Lovelace" : null, document_version: 1, amount: { amount: `${n}00.5000`, currency: "USD" }, step: { index: 1, of: 2 },
    due_at: "2030-01-01T00:00:00Z", decided_at: null, status_word: "Your Turn", on_behalf_of_email: null, on_behalf_of_name: null, href: `/requisitions/${n}`, ...patch,
  };
}

export type ApprovalsState = Awaited<ReturnType<typeof stubApprovals>>;

export async function stubApprovals(page: Page, options: { delegateAdmin?: boolean } = {}) {
  await stubProjects(page);
  const state = {
    pending: [inboxRow(1), inboxRow(2), inboxRow(3, { due_at: "2020-01-01T00:00:00Z", status_word: "Overdue" })],
    completed: [inboxRow(4, { status_word: "Approved", decided_at: "2026-10-12T10:00:00Z", on_behalf_of_email: "owner@example.test", on_behalf_of_name: "Grace Hopper" })],
    delegated: [inboxRow(5, { status_word: "Covering owner@example.test", on_behalf_of_email: "owner@example.test" })],
    delegations: [] as components["schemas"]["DelegationRead"][],
    mode: "ok" as "ok" | "error" | "loading" | "empty",
    decision: "ok" as "ok" | "stale" | "closed",
    explanationFails: false,
    closedInstance: false,
    revokeFails: false,
    behalf: [] as components["schemas"]["DecisionRead"][],
    behalfFails: false,
    eligibleQueries: [] as URLSearchParams[],
    pageTwo: false,
    totalAmount: [{ amount: "601.5000", currency: "USD" }],
    version: 1,
    evidence: {} as Record<string, Record<string, unknown>>,
    tasksQueries: [] as URLSearchParams[],
    posts: [] as { path: string; body: Record<string, unknown>; headers: Record<string, string> }[],
  };
  const person = { user_id: uuid(900), email: "delegate@example.test" };
  await page.route("**/api/v1/approvals/**", async (route) => {
    const request = route.request(), url = new URL(request.url()), path = url.pathname, method = request.method();
    const send = (value: unknown, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(value) });
    const problem = (detail: string, status: number, checks?: Record<string, string>, errors?: unknown[]) =>
      send({ detail, status, correlation_id: "ref-1234", type: "about:blank", title: "Request failed", checks, errors }, status);
    if (path === "/api/v1/approvals/tasks") {
      state.tasksQueries.push(url.searchParams);
      if (state.mode === "loading") await new Promise((resolve) => setTimeout(resolve, 800));
      if (state.mode === "error") return problem("The service is unavailable.", 503);
      if (state.pageTwo && url.searchParams.has("cursor")) return problem("The service is unavailable.", 503);
      const view = url.searchParams.get("view") ?? "pending";
      let rows = state.mode === "empty" ? [] : view === "completed" ? state.completed : view === "delegated" ? state.delegated : view === "overdue" ? state.pending.filter((r) => r.status_word === "Overdue") : state.pending;
      const q = url.searchParams.get("q")?.toLowerCase();
      if (q) rows = rows.filter((r) => r.number.toLowerCase().startsWith(q) || r.title.toLowerCase().startsWith(q));
      if (url.searchParams.get("type") === "po") rows = [];
      if (url.searchParams.get("sort") === "amount") rows = [...rows].sort((a, b) => Number(a.amount?.amount) * (url.searchParams.get("direction") === "desc" ? -1 : 1) - Number(b.amount?.amount) * (url.searchParams.get("direction") === "desc" ? -1 : 1));
      if (state.decision === "stale" && url.searchParams.get("q")) rows = rows.map((r) => ({ ...r, document_version: 2 }));
      return send({
        rows: state.pageTwo && !url.searchParams.has("cursor") ? rows.slice(0, 1) : rows, total: rows.length, total_amount: state.mode === "empty" ? [] : state.totalAmount,
        counts: { pending: state.pending.length, overdue: 1, completed: state.completed.length, delegated: state.delegated.length },
        next_cursor: state.pageTwo && !url.searchParams.has("cursor") ? "page2" : null,
      });
    }
    const task = /^\/api\/v1\/approvals\/tasks\/([^/]+)\/decisions$/.exec(path);
    if (task && method === "POST") {
      const body = request.postDataJSON() as Record<string, unknown>;
      state.posts.push({ path, body, headers: request.headers() });
      if (state.decision === "stale") return problem("This document changed after you opened it.", 409, { problem: "stale_version" });
      if (state.decision === "closed") return problem("This approval task is closed.", 409, { problem: "task_closed" });
      const row = [...state.pending, ...state.completed].find((r) => r.task_id === task[1])!;
      if (body.outcome !== "comment") state.pending = state.pending.filter((r) => r.task_id !== task[1]);
      return send({
        decision: { id: uuid(700), instance_id: row.instance_id, task_id: row.task_id, node_id: "one", step_label: "Finance", actor_email: "me@example.test", on_behalf_of_email: null, authority: "member", outcome: body.outcome, reject_mode: body.reject_mode ?? null, comment: body.comment ?? null, document_version: body.document_version, members_at_decision: [], decided_at: "2026-10-13T09:00:00Z" },
        instance: { id: row.instance_id, status: "approved" },
      });
    }
    const instance = /^\/api\/v1\/approvals\/instances\/([^/]+)(\/decisions|\/explanation)?$/.exec(path);
    if (instance) {
      const row = [...state.pending, ...state.completed, ...state.delegated].find((r) => r.instance_id === instance[1])!;
      if (instance[2] === "/decisions") return send([{ id: uuid(800), instance_id: row.instance_id, task_id: null, node_id: null, step_label: null, actor_email: requester, on_behalf_of_email: null, authority: "requester", outcome: "comment", reject_mode: null, comment: "Please review", document_version: 1, members_at_decision: [], decided_at: "2026-10-12T08:00:00Z" }]);
      if (instance[2] === "/explanation") {
        if (state.explanationFails) return problem("Explanation offline.", 503);
        return send({ instance_id: row.instance_id, why_this_workflow: "Capital Review applies to requisitions.", workflow: { bound_scope: "organization", definition_id: uuid(1), name: "Capital Review", version: 1 }, steps: [{ kind: "approval", node_id: "one", name: "Finance", rule: "role approver", mode: "any", status: "open", members_as_of: "as_of_now", members: [{ basis: "role", email: "me@example.test", user_id: uuid(1) }], excluded: [{ user_id: uuid(5), email: "requester@example.test", reason: "separation_of_duty", detail: "Requested this document, so cannot approve it" }], decisions: [] }] });
      }
      const open = !state.closedInstance && !state.completed.some((r) => r.instance_id === row.instance_id);
      return send({ id: row.instance_id, document_type: row.document_type, document_id: row.document_id, workflow_version: 1, workflow_version_id: uuid(2), document_version: 1, status: open ? "running" : "approved", requester_email: requester, requester_name: null, current_node_id: "one", route: [], tasks: [{ id: row.task_id, node_id: "one", label: "Finance", mode: "any", quorum: null, status: open ? "open" : "completed", outcome: null, approver_emails: [], approver_names: [], due_at: row.due_at, completed_at: null }], started_at: "2026-10-12T00:00:00Z", finished_at: null, separation_of_duty_applied: true, evidence: state.evidence[row.instance_id] ?? null });
    }
    if (path === "/api/v1/approvals/delegations/eligible-delegates") {
      state.eligibleQueries.push(url.searchParams);
      // The colleague may approve everything except purchase orders: asking for them lists no one.
      const asked = url.searchParams.getAll("document_types");
      return send({ rows: asked.length > 0 ? [] : [person] });
    }
    if (path === "/api/v1/approvals/delegations/decided-on-my-behalf") {
      if (state.behalfFails) return problem("Offline.", 503);
      return send(state.behalf);
    }
    const revoke = /^\/api\/v1\/approvals\/delegations\/([^/]+)\/revoke$/.exec(path);
    if (revoke && state.revokeFails) return problem("The service is unavailable.", 503);
    if (revoke) {
      state.posts.push({ path, body: {}, headers: request.headers() });
      state.delegations = state.delegations.map((d) => (d.id === revoke[1] ? { ...d, state: "revoked", revoked_at: "2026-10-13T09:00:00Z" } : d));
      return send(state.delegations.find((d) => d.id === revoke[1]));
    }
    if (path === "/api/v1/approvals/delegations" && method === "POST") {
      const body = request.postDataJSON() as Record<string, unknown>;
      state.posts.push({ path, body, headers: request.headers() });
      if (body.reason === "overlap") return problem("You already have a delegation in this period. Revoke it or choose other dates.", 409, { problem: "delegation_overlaps" });
      const created = { id: uuid(600 + state.delegations.length), delegator_email: "me@example.test", delegate_email: person.email, starts_at: body.starts_at, ends_at: body.ends_at, document_types: body.document_types ?? null, reason: body.reason, created_at: "2026-10-13T09:00:00Z", revoked_at: null, state: "scheduled" } as components["schemas"]["DelegationRead"];
      state.delegations = [...state.delegations, created];
      return send(created, 201);
    }
    if (path === "/api/v1/approvals/delegations") {
      const view = url.searchParams.get("view");
      if (view === "organization" && !options.delegateAdmin) return problem("Not allowed.", 403);
      return send({ rows: view === "covering" ? [] : state.delegations, next_cursor: null });
    }
    return route.fallback();
  });
  return state;
}
