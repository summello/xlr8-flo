import { useRef, useState } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import { CaretRight } from "@phosphor-icons/react";
import type { components } from "../../api/generated/schema";
import StatusPill from "../status/StatusPill";
type Node = components["schemas"]["TreeNode"];
export default function Hierarchy({ tree, selected, navigate }: { tree: Node; selected: string; navigate: (href: string) => void }) {
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set());
  const [focused, setFocused] = useState(tree.id);
  const scrollRef = useRef<HTMLDivElement>(null);
  const refs = useRef(new Map<string, HTMLDivElement>());
  const rows: { node: Node; level: number; parent?: string }[] = [];
  const visit = (node: Node, level: number, parent?: string) => {
    rows.push({ node, level, parent });
    if (expanded.has(node.id)) for (const child of node.children ?? []) visit(child, level + 1, node.id);
  };
  visit(tree, 1);
  const virtual = rows.length > 50;
  // TanStack Virtual owns imperative measurements, as in the shared DataGrid.
  // eslint-disable-next-line react-hooks/incompatible-library
  const rowVirtualizer = useVirtualizer({ count: virtual ? rows.length : 0, getScrollElement: () => scrollRef.current, estimateSize: () => parseInt(getComputedStyle(document.documentElement).getPropertyValue("--row-h"), 10) * 3, getItemKey: index => rows[index]?.node.id ?? index, overscan: 8 });
  const displayed = virtual ? rowVirtualizer.getVirtualItems().map(item => ({ ...rows[item.index]!, index: item.index, offset: item.start })) : rows.map((row, index) => ({ ...row, index, offset: undefined }));
  const toggle = (id: string, open: boolean) => setExpanded(current => { const next = new Set(current); if (open) next.add(id); else next.delete(id); return next; });
  const focus = (id: string | undefined) => { if (id) {
    setFocused(id);
    if (virtual) rowVirtualizer.scrollToIndex(rows.findIndex(row => row.node.id === id), { align: "auto" });
    requestAnimationFrame(() => refs.current.get(id)?.focus());
  } };
  return <div ref={scrollRef} role="treegrid" aria-label="Project hierarchy" aria-rowcount={rows.length} className="dashboard-tree"><div role="rowgroup" style={virtual ? { height: rowVirtualizer.getTotalSize(), position: "relative" } : undefined}>{displayed.map(({ node, level, parent, index, offset }) => <div key={node.id} data-index={index} ref={element => { if (element) { refs.current.set(node.id, element); if (virtual) requestAnimationFrame(() => { if (element.isConnected) rowVirtualizer.measureElement(element); }); } else refs.current.delete(node.id); }} role="row" aria-rowindex={index + 1} aria-level={level} aria-expanded={node.children?.length ? expanded.has(node.id) : undefined} aria-selected={node.id === selected} tabIndex={focused === node.id ? 0 : -1} onFocus={() => setFocused(node.id)} onKeyDown={e => {
    if (e.target !== e.currentTarget && e.key === "Enter") return;
    if (!["ArrowRight", "ArrowLeft", "ArrowUp", "ArrowDown", "Home", "End", "Enter"].includes(e.key)) return;
    e.preventDefault();
    if (e.key === "ArrowRight") { if (node.children?.length && !expanded.has(node.id)) toggle(node.id, true); else focus(node.children?.[0]?.id); }
    if (e.key === "ArrowLeft") { if (expanded.has(node.id)) toggle(node.id, false); else focus(parent); }
    if (e.key === "ArrowDown") focus(rows[index + 1]?.node.id);
    if (e.key === "ArrowUp") focus(rows[index - 1]?.node.id);
    if (e.key === "Home") focus(rows[0]?.node.id);
    if (e.key === "End") focus(rows.at(-1)?.node.id);
    if (e.key === "Enter") navigate(`/projects/${node.id}/dashboard`);
  }} style={{ paddingInlineStart: `calc(var(--space-4) * ${level})`, ...(offset === undefined ? {} : { position: "absolute", width: "100%", top: 0, transform: `translateY(${offset}px)` }) }}>
    <div role="gridcell">{!!node.children?.length && <button aria-label={`${expanded.has(node.id) ? "Collapse" : "Expand"} ${node.name}`} onClick={() => toggle(node.id, !expanded.has(node.id))}><CaretRight weight="regular" aria-hidden="true" /></button>}<a href={`/projects/${node.id}/dashboard`} onClick={e => { e.preventDefault(); navigate(e.currentTarget.getAttribute("href")!); }}>{node.number} {node.name}</a><p>{node.consumption_percent === null || node.consumption_percent === undefined ? "No budget allocated" : `${node.consumption_percent}% consumed`}</p>{node.consumption_percent != null && <meter aria-label={`${node.name} consumed share`} min="0" max="100" value={node.consumption_percent} />}</div>
    <div role="gridcell"><StatusPill docType="project" status={node.status} /></div>
  </div>)}</div></div>;
}
