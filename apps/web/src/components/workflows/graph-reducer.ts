import type { components } from "../../api/generated/schema";
export type Graph = components["schemas"]["WorkflowGraph"];
export type Node = components["schemas"]["Node"];
export type Edge = components["schemas"]["Edge"];
export type Output = "next" | "yes" | "no";
export type Operation = {
    type: "add";
    node: Node;
} | {
    type: "move";
    id: string;
    x: number;
    y: number;
} | {
    type: "update";
    id: string;
    fields: Partial<Node>;
} | {
    type: "condition";
    id: string;
    when: NonNullable<Edge["when"]>;
} | {
    type: "delete";
    id: string;
} | {
    type: "connect";
    from: string;
    output: Output;
    to: string;
} | {
    type: "disconnect";
    from: string;
    output: Output;
};
export const outputs = (node: Node): Output[] => node.kind === "end" ? [] : node.kind === "condition" ? ["yes", "no"] : ["next"];
export const edgeOutput = (edge: Edge): Output => edge.default ? "no" : edge.when ? "yes" : "next";
export function makesLoop(graph: Graph, from: string, to: string): boolean {
    const visited = new Set<string>();
    const stack = [to];
    while (stack.length) {
        const id = stack.pop()!;
        if (id === from)
            return true;
        if (visited.has(id))
            continue;
        visited.add(id);
        stack.push(...graph.edges.filter(e => e.from === id).map(e => e.to));
    }
    return false;
}
export function reducer(graph: Graph, op: Operation): Graph {
    switch (op.type) {
        case "add": return { ...graph, nodes: [...graph.nodes, op.node] };
        case "move": return { ...graph, layout: { ...graph.layout, [op.id]: { x: op.x, y: op.y } } };
        case "update": return { ...graph, nodes: graph.nodes.map(n => n.id === op.id ? { ...n, ...op.fields, id: n.id, kind: n.kind } : n) };
        case "condition": return { ...graph, edges: graph.edges.map(e => e.from === op.id && edgeOutput(e) === "yes" ? { ...e, when: op.when } : e) };
        case "delete": {
            const layout = { ...graph.layout };
            delete layout[op.id];
            return { ...graph, layout, nodes: graph.nodes.filter(n => n.id !== op.id), edges: graph.edges.filter(e => e.from !== op.id && e.to !== op.id) };
        }
        case "disconnect": return { ...graph, edges: graph.edges.filter(e => e.from !== op.from || edgeOutput(e) !== op.output) };
        case "connect": {
            const previous = graph.edges.find(e => e.from === op.from && edgeOutput(e) === op.output);
            return { ...graph, edges: [...graph.edges.filter(e => e.from !== op.from || edgeOutput(e) !== op.output), {
                        from: op.from, to: op.to, default: op.output === "no",
                        ...(op.output === "yes" ? { when: previous?.when ?? { field: "", op: "gt", value: { amount: "", currency: "" } } } : {}),
                    }] };
        }
    }
}
// Every editor submits through this boundary; loop violations never reach the reducer.
export function operate(graph: Graph, op: Operation): {
    graph: Graph;
    error?: string;
} {
    if (op.type === "connect" && makesLoop(graph, op.from, op.to)) {
        const name = (id: string) => graph.nodes.find(n => n.id === id)?.name || id;
        return { graph, error: `Not connected. That would make a loop. ${name(op.from)} to ${name(op.to)}.` };
    }
    return { graph: reducer(graph, op) };
}
export function routeOrder(graph: Graph): Node[] {
    const ids: string[] = [];
    const visited = new Set<string>();
    function visit(id: string) { if (visited.has(id))
        return; visited.add(id); ids.push(id); graph.edges.filter(e => e.from === id).forEach(e => visit(e.to)); }
    graph.nodes.filter(n => n.kind === "start").forEach(n => visit(n.id));
    graph.nodes.forEach(n => visit(n.id));
    return ids.map(id => graph.nodes.find(n => n.id === id)!);
}
export function summary(node: Node) {
    if (node.kind !== "approval")
        return node.kind === "condition" ? "Yes / No routes" : node.kind === "start" ? "Choose approvers" : "Route complete";
    return `${typeof node.mode === "object" ? `Quorum ${node.mode?.quorum}` : node.mode === "all" ? "All" : "Any one"} · ${node.approvers?.type === "users" ? node.approvers.user_ids.length : node.approvers?.type ?? "No"} approvers${node.due_days ? ` · due ${node.due_days} days` : ""}`;
}

export function pointerMove(id: string, initial: {x: number; y: number}, delta: {x: number; y: number}, grid: number): Operation {
 return { type:"move",id,x:Math.max(0,Math.round((initial.x+delta.x)/grid)*grid),y:Math.max(0,Math.round((initial.y+delta.y)/grid)*grid) };
}
export function keyboardMove(id: string, initial: {x: number; y: number}, direction: [number,number], grid: number, shift: boolean): Operation {
 return {type:"move",id,x:Math.max(0,initial.x+direction[0]*grid*(shift?5:1)),y:Math.max(0,initial.y+direction[1]*grid*(shift?5:1))};
}
