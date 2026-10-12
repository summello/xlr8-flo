import { expect, test } from "vitest";
import { graphDiff } from "./graph-diff";
import type { components } from "../../../api/generated/schema";
test("diff lists added removed steps, condition value and approver changes without modifying graphs", () => {
    const before: components["schemas"]["WorkflowGraph"] = { nodes: [{ id: "old", kind: "approval", name: "Old" }, { id: "review", kind: "approval", name: "Review", approvers: { type: "users", user_ids: ["a"] } }], edges: [{ from: "gate", to: "review", when: { field: "amount", op: "gt", value: { amount: "100", currency: "USD" } } }] };
    const after = structuredClone(before);
    after.nodes[0] = { id: "new", kind: "approval", name: "New" };
    after.nodes[1]!.approvers = { type: "users", user_ids: ["b"] };
    after.edges[0]!.when = { field: "amount", op: "gt", value: { amount: "200", currency: "USD" } };
    const original = structuredClone(before), rows = graphDiff(before, after);
    expect(rows).toEqual(expect.arrayContaining([expect.objectContaining({ field: "Step Old", change: "Removed" }), expect.objectContaining({ field: "Step New", change: "Added" }), expect.objectContaining({ field: "Review · Approvers", change: "Changed" }), expect.objectContaining({ field: "Condition / connection gate → review", change: "Changed" })]));
    expect(before).toEqual(original);
});
test("unchanged graphs have no diff and layout alone is not a routing change", () => { const g: components["schemas"]["WorkflowGraph"] = { nodes: [{ id: "start", kind: "start" }], edges: [] }; expect(graphDiff(g, { ...g, layout: { start: { x: 10, y: 20 } } })).toEqual([]); });
