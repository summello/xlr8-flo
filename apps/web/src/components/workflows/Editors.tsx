import { ArrowRight, GitBranch, Play, Stop, Users, WarningCircle, Trash } from "@phosphor-icons/react";
import { useRef, type Dispatch } from "react";
import { pointerMove, keyboardMove, edgeOutput, outputs, routeOrder, summary, type Graph, type Node, type Operation } from "./graph-reducer";
import type { components } from "../../api/generated/schema";
const icons = { start: Play, end: Stop, condition: GitBranch, approval: Users };
type Props = {
    graph: Graph;
    selected: string;
    select: (id: string, edit?: boolean) => void;
    dispatch: Dispatch<Operation>;
    issues: components["schemas"]["Issue"][];
};
export function Connections({ graph, node, dispatch }: {
    graph: Graph;
    node: Node;
    dispatch: Dispatch<Operation>;
}) {
    return <>{outputs(node).map(output => <label key={output}>{output === "next" ? "Goes To" : `${output === "yes" ? "Yes" : "No"} Goes To`}<select aria-label={`${node.name || node.id} ${output === "next" ? "Goes To" : `${output === "yes" ? "Yes" : "No"} Goes To`}`} value={graph.edges.find(e => e.from === node.id && edgeOutput(e) === output)?.to ?? ""} onChange={e => dispatch(e.target.value ? { type: "connect", from: node.id, output, to: e.target.value } : { type: "disconnect", from: node.id, output })}>
    <option value="">Not connected</option>{routeOrder(graph).filter(n => n.id !== node.id && n.kind !== "start").map(n => <option key={n.id} value={n.id}>{n.name || n.id}</option>)}
  </select></label>)}</>;
}
export function Outline({ graph, select, dispatch }: Props) {
    return <table className="wf-outline"><caption>Workflow outline</caption><thead><tr>{["Node", "Type", "Summary", "Goes To", "Actions"].map(h => <th key={h}>{h}</th>)}</tr></thead>
    <tbody>{routeOrder(graph).map(n => <tr key={n.id}><th scope="row">{n.name || n.id}</th><td>{n.kind}</td><td>{summary(n)}</td><td><Connections graph={graph} node={n} dispatch={dispatch}/></td><td><button onClick={() => select(n.id, true)}>Edit {n.name || n.id}</button><button onClick={() => dispatch({ type: "delete", id: n.id })}><Trash aria-hidden="true"/>Delete {n.name || n.id}</button></td></tr>)}</tbody></table>;
}
export function Canvas({ graph, selected, select, dispatch, issues }: Props) {
    const drag = useRef<{
        id: string;
        x: number;
        y: number;
        initialX: number;
        initialY: number;
    } | null>(null);
    const port = useRef<{
        from: string;
        output: ReturnType<typeof outputs>[number];
    } | null>(null);
    const position = (id: string) => graph.layout?.[id] ?? { x: 40 + graph.nodes.findIndex(n => n.id === id) * 240, y: 40 };
    const grid = () => Number.parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--space-5"));
    // The node is ten grid units wide (workflows.css), so label anchors follow the token.
    const node = grid() * 10;
    return <div className="wf-board" aria-label="Workflow canvas"><div className="wf-plane" style={{ width: Math.max(1000, ...graph.nodes.map(n => position(n.id).x + 240)), height: Math.max(480, ...graph.nodes.map(n => position(n.id).y + 200)) }}>
    <svg className="wf-lines" aria-hidden="true"><defs><marker id="wf-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z"/></marker></defs>{graph.edges.map((e, i) => { const a = position(e.from), b = position(e.to); return <g key={i}><path d={`M ${a.x + 100} ${a.y + 90} L ${b.x + 100} ${b.y}`} markerEnd="url(#wf-arrow)"/>{edgeOutput(e) !== "next" && <text x={a.x + (edgeOutput(e) === "yes" ? node * 0.25 : node * 0.75)} y={a.y + 120} textAnchor="middle">{edgeOutput(e) === "yes" ? "Yes" : "No"}</text>}</g>; })}</svg>
    {routeOrder(graph).map(n => {
            const Icon = icons[n.kind];
            const p = position(n.id);
            const problems = issues.filter(i => i.node_id === n.id);
            return <div className="wf-node-wrap" key={n.id} style={{ left: p.x, top: p.y }}>
      <button className="wf-node" data-node={n.id} aria-pressed={selected === n.id} aria-label={`${n.kind} ${n.name || n.id}, ${summary(n)}${problems.map(i => `, ${i.code}`).join("")}`} onClick={() => select(n.id)} onPointerDown={e => { if (e.button !== 0)
                return; e.currentTarget.setPointerCapture(e.pointerId); drag.current = { id: n.id, x: e.clientX, y: e.clientY, initialX: p.x, initialY: p.y }; select(n.id); }} onPointerMove={e => { const d = drag.current; if (!d || d.id !== n.id)
                return; const step = grid(); dispatch(pointerMove(n.id, {x:d.initialX,y:d.initialY}, {x:e.clientX-d.x,y:e.clientY-d.y}, step)); }} onPointerUp={() => { drag.current = null; }} onPointerCancel={() => { drag.current = null; }} onKeyDown={e => { const delta: Record<string, [
                number,
                number
            ]> = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] }; if (delta[e.key]) {
                e.preventDefault();
                const [x, y] = delta[e.key]!;
                dispatch(keyboardMove(n.id,p,[x,y],grid(),e.shiftKey));
            } if (e.key === "Delete") {
                e.preventDefault();
                dispatch({ type: "delete", id: n.id });
            } }}>
        <span><Icon aria-hidden="true"/>{n.kind}</span><strong>{n.name || n.id}</strong><span>{summary(n)}</span>{problems.map((i, j) => <span key={j}><WarningCircle aria-hidden="true"/>{i.code.replaceAll("_", " ")}</span>)}
      </button>
      <button aria-label={`Connect output from ${n.name || n.id}`} onClick={() => select(n.id, true)}><ArrowRight aria-hidden="true"/>Connect output to…</button>
      {outputs(n).map(output => <span key={output} className="wf-port" aria-hidden="true" onPointerDown={e => { e.preventDefault(); e.currentTarget.setPointerCapture(e.pointerId); port.current = { from: n.id, output }; }} onPointerUp={e => { const source = port.current; port.current = null; const target = document.elementFromPoint(e.clientX, e.clientY)?.closest<HTMLElement>("[data-node]")?.dataset.node; if (source && target)
                dispatch({ type: "connect", ...source, to: target }); }} onPointerCancel={() => { port.current = null; }}>{output === "next" ? "Output" : output === "yes" ? "Yes" : "No"}</span>)}
    </div>;
        })}
  </div></div>;
}
