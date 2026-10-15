import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { pointerMove, keyboardMove, operate, type Graph, type Operation } from "./graph-reducer";
const graph: Graph = { nodes: [{ id: "start", kind: "start" }, { id: "end", kind: "end" }], edges: [], layout: {} };
const sequence: Operation[] = [
    { type: "add", node: { id: "condition", kind: "condition", name: "Estimate" } },
    { type: "add", node: { id: "approval", kind: "approval", approvers: { type: "permission" }, mode: "any" } },
    { type: "connect", from: "start", output: "next", to: "condition" },
    { type: "connect", from: "condition", output: "yes", to: "approval" },
    { type: "condition", id: "condition", when: { field: "amount", op: "gt", value: { amount: "100", currency: "USD" } } },
    { type: "connect", from: "condition", output: "no", to: "end" },
    { type: "connect", from: "approval", output: "next", to: "end" },
    { type: "move", id: "approval", x: 100, y: 200 },
    { type: "update", id: "approval", fields: { name: "Review", allow_repeat: true } },
    { type: "disconnect", from: "condition", output: "no" },
    { type: "connect", from: "condition", output: "no", to: "end" },
    { type: "add", node: { id: "extra", kind: "end" } }, { type: "delete", id: "extra" },
];
describe("shared workflow operations", () => {
    it("canvas keyboard and Outline operation sequences produce identical graphs without mutation", () => {
        const original = JSON.stringify(graph);
        const moveInputs = [pointerMove("approval",{x:80,y:180},{x:21,y:21},20),keyboardMove("approval",{x:80,y:180},[1,1],20,false),{type:"move",id:"approval",x:100,y:200} as Operation];
        const outputs = moveInputs.map(move => sequence.map(op=>op.type==="move"?move:op).reduce((g,op)=>operate(g,op).graph,structuredClone(graph)));
        expect(outputs[0]).toEqual(outputs[1]);
        expect(outputs[1]).toEqual(outputs[2]);
        expect(JSON.stringify(graph)).toBe(original);
        expect(outputs[0]?.layout?.approval).toEqual({ x: 100, y: 200 });
        // Enforce the editor boundary in addition to testing reducer semantics.
        const source = readFileSync(new URL("./Editors.tsx", import.meta.url), "utf8");
        expect(source).not.toMatch(/graph\.(?:nodes|edges|layout)\s*(?:\[[^\]]+\])?\s*=|graph\.(?:nodes|edges)\.(?:push|splice)/);
        expect(source).toContain('dispatch(pointerMove(');
        expect(source).toContain('dispatch(keyboardMove(');
        expect(source).toContain('dispatch({ type: "connect"');
    });
    it("loop prevention refuses the connection before reduction and names both nodes", () => {
        const built = sequence.reduce((g, op) => operate(g, op).graph, structuredClone(graph));
        const refused = operate(built, { type: "connect", from: "approval", output: "next", to: "condition" });
        expect(refused.graph).toBe(built);
        expect(refused.error).toContain("Not connected. That would make a loop.");
        expect(refused.error).toContain("Review to Estimate");
        expect(operate(built, { type: "connect", from: "approval", output: "next", to: "approval" }).graph).toBe(built);
    });
    it("deleting a node removes its connections and layout", () => { const built = sequence.reduce((g, op) => operate(g, op).graph, structuredClone(graph)); const result = operate(built, { type: "delete", id: "approval" }).graph; expect(result.nodes.some(n => n.id === "approval")).toBe(false); expect(result.edges.some(e => e.from === "approval" || e.to === "approval")).toBe(false); expect(result.layout?.approval).toBeUndefined(); });
});
