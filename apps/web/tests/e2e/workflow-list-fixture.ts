import type { Page } from "@playwright/test";
import { stubBuilder, workflowId, approverId } from "./workflow-builder-fixture";
export { workflowId };
export async function stubWorkflows(page: Page) {
    const builder = await stubBuilder(page);
    builder.graph = { nodes: [{ id: "start", kind: "start", name: "Start" }, { id: "gate", kind: "condition", name: "Estimate" }, { id: "review", kind: "approval", name: "Finance", approvers: { type: "users", user_ids: [approverId] }, mode: "any" }, { id: "end", kind: "end", name: "End" }], edges: [{ from: "start", to: "gate" }, { from: "gate", to: "review", when: { field: "amount", op: "gt", value: { amount: "50000", currency: "USD" } } }, { from: "gate", to: "end", default: true }, { from: "review", to: "end" }] };
    builder.graph.nodes.splice(-1, 0, {id:"added", kind:"approval", name:"Added step", approvers:{type:"users", user_ids:[approverId]}, mode:"all", allow_repeat:true});
    builder.graph.edges = builder.graph.edges.filter(e=>e.from!=="review");
    builder.graph.edges.push({from:"review",to:"added"},{from:"added",to:"end"});
    const w = { id: workflowId, name: "Capital Review", document_type: "requisition", scope_type: "org", scope_id: workflowId, created_by: approverId, created_at: "2026-10-12T00:00:00Z", retired_at: null, applies_to: "requisition · Northstar", published_version: 1, draft_version: 2, in_flight: 3, edited_at: "2026-10-12T00:00:00Z", edited_by_name: "admin@example.test", versions: [{ version: 1, status: "published", created_at: "2026-10-10T00:00:00Z", published_at: "2026-10-10T00:00:00Z", author_name: "admin@example.test", in_flight: 3 }, { version: 2, status: "draft", created_at: "2026-10-12T00:00:00Z", published_at: null, author_name: "admin@example.test", in_flight: 0 }] };
    const state = { publishedOnly: false, mode: "ready", failPreview: false, blockedPreview: false, failHistory: false, failUnits: false, queries: [] as URL[], writes: builder.writes };
    await page.route("**/api/v1/**", async (route) => {
        const u = new URL(route.request().url()), path = u.pathname;
        let body: unknown, status = 200;
        if (path === "/api/v1/workflows") {
            state.queries.push(u);
            if (route.request().method() === "POST") {
                state.writes.push({method:"POST",path,body:route.request().postDataJSON(),headers:route.request().headers()});
                if (!route.request().postDataJSON().name) {status=422;body={detail:"Enter a workflow name.",errors:[{field:"/name",message:"Enter a workflow name."}]};}
                else body = w;
            }
            else if (state.mode === "error") {
                status = 503;
                body = { detail: "Offline" };
            }
            else
                body = { rows: state.mode === "empty" || u.searchParams.get("search") === "missing" ? [] : [{ ...w, versions: [], in_flight: state.mode === "partial" ? null : 3 }], next_cursor: state.mode === "paged" && !u.searchParams.has("cursor") ? "next" : null };
            if (state.mode === "loading")
                await new Promise(resolve => setTimeout(resolve, 500));
        }
        else if (path === `/api/v1/workflows/${workflowId}`) {
            body = state.publishedOnly ? { ...w, draft_version: null, versions: w.versions.filter(v => v.status === "published") } : w;
            if (state.failHistory) {
                status = 503;
                body = { detail: "History offline" };
            }
        }
        else if (path.endsWith("/route-preview")) {
            state.queries.push(u);
            const amount = u.searchParams.get("amount")!;
            if (state.failPreview || !/^\d{1,14}(\.\d{1,4})?$/.test(amount)) {
                status = 422;
                body = { detail: "Enter a valid estimate.", errors: [{ field: "/amount", message: "Enter a valid estimate." }] };
            }
            else
                body = { workflow_version_id: workflowId, workflow_version: 2, separation_of_duty_applied: false, path_edge_indices: [0, 1, 3, 4], steps: [{ node_id: "review", label: "Finance", reached_via: "Estimate is over 50,000.00 USD", mode: "any", quorum: null, rule: "listed by name", approver_emails: ["approver@example.test"], approver_names: ["Ada Lovelace"], excluded: [{ email: "excluded@example.test", name: null, reason: "not_eligible_in_scope" }], state: "open", due_at: null }, {node_id:"added",label:"Added step",reached_via:"Estimate is over 50,000.00 USD",mode:"all",quorum:null,rule:"listed by name",approver_emails:["approver@example.test"],approver_names:[null],excluded:[],state:"upcoming",due_at:null}], problems: [] };
        }
        else if (path === `/api/v1/workflows/${workflowId}/versions/1`) {
            body = { ...w.versions[0], id: workflowId, definition_id: workflowId, created_by: approverId, published_by: approverId, row_version: 1, graph: { ...builder.graph, nodes: [...builder.graph.nodes.filter(n=>n.id!=="added").map(n => n.id === "review" ? {...n, approvers: {type: "users", user_ids: [workflowId]}} : n), { id: "removed", kind: "approval", name: "Removed step", approvers:{type:"users",user_ids:[approverId]}, mode:"all", allow_repeat:true }], edges: [{from:"start",to:"gate"},{ from: "gate", to: "review", when: { field: "amount", op: "gt", value: { amount: "10000", currency: "USD" } } },{from:"gate",to:"end",default:true},{from:"review",to:"removed"},{from:"removed",to:"end"}] } };
        }
        else if (path === `/api/v1/workflows/${workflowId}/versions/2`) {
            body = { ...w.versions[1], id: workflowId, definition_id: workflowId, created_by: approverId, published_by: null, row_version: 1, graph: builder.graph };
        }
        else if (path === "/api/v1/org/units") {
            body = { rows: [{ id: approverId, name: "Infrastructure", kind: "bu" }], next_cursor: null };
            if (state.failUnits) {
                status = 503;
                body = { detail: "Units offline" };
            }
        }
        else {
            await route.fallback();
            return;
        }
        if (path.endsWith("/route-preview") && status === 200 && (u.searchParams.get("amount") === "100" || state.blockedPreview)) {
            body = {workflow_version_id:workflowId,workflow_version:2,separation_of_duty_applied:false,path_edge_indices:[0,2],steps:[],problems:state.blockedPreview ? [{code:"no_eligible_approver",step_label:"Finance",message:"No eligible approver for Finance. Ask a workflow administrator."}] : []};
        }
        await route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
    });
    return state;
}
