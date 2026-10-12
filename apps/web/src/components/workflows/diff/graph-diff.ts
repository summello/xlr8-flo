import type { components } from "../../../api/generated/schema";
type Graph = components["schemas"]["WorkflowGraph"];
export type Difference = {
    field: string;
    before: string;
    after: string;
    change: "Added" | "Removed" | "Changed";
};
function stable(value: unknown): string {
    if (Array.isArray(value))
        return `[${value.map(stable).join(",")}]`;
    if (value && typeof value === "object")
        return `{${Object.entries(value).sort(([a], [b]) => a.localeCompare(b)).map(([k, v]) => `${k}:${stable(v)}`).join(",")}}`;
    return JSON.stringify(value) ?? "None";
}
function present(value: unknown): string {
    if (value === undefined || value === null) return "None";
    if (Array.isArray(value)) return value.map(present).join(", ");
    if (typeof value === "boolean") return value ? "Yes" : "No";
    if (typeof value !== "object") return String(value);
    const object = value as Record<string, unknown>;
    if (typeof object.amount === "string" && typeof object.currency === "string") return `${object.amount} ${object.currency}`;
    if (typeof object.field === "string") {
        const words: Record<string,string> = {eq:"is",ne:"is not",lt:"is below",le:"is at most",gt:"is over",ge:"is at least",in:"is one of",not_in:"is not one of",contains:"contains",is_null:"is empty",not_null:"is not empty"};
        return `${object.field === "amount" ? "Estimate" : object.field} ${words[String(object.op)] ?? String(object.op)}${object.value === undefined ? "" : ` ${present(object.value)}`}`;
    }
    if (Array.isArray(object.all)) return object.all.map(present).join(" and ");
    if (Array.isArray(object.any)) return object.any.map(present).join(" or ");
    if (object.not) return `Not (${present(object.not)})`;
    if (object.type === "users") return `Listed people: ${present(object.user_ids)}`;
    if (object.type === "role") return `Role: ${present(object.role_code)}`;
    if (object.type === "permission") return "Current permission holders";
    if (object.quorum !== undefined) return `${present(object.quorum)} approvals needed`;
    if (object.from && object.to) return `${object.from} → ${object.to}${object.default ? " (default)" : ""}`;
    return Object.entries(object).map(([key,item]) => `${key.replaceAll("_"," ")}: ${present(item)}`).join("; ");
}
export function graphDiff(before: Graph, after: Graph): Difference[] {
    const rows: Difference[] = [];
    for (const id of new Set([...before.nodes, ...after.nodes].map(n => n.id))) {
        const a = before.nodes.find(n => n.id === id), b = after.nodes.find(n => n.id === id);
        if (!a || !b) {
            rows.push({ field: `Step ${b?.name || a?.name || id}`, before: a ? a.name || id : "None", after: b ? b.name || id : "None", change: a ? "Removed" : "Added" });
            continue;
        }
        for (const key of ["kind", "name", "mode", "approvers", "allow_repeat", "due_days", "escalate_after_days", "escalate_to"] as const) {
            if (stable(a[key]) !== stable(b[key]))
                rows.push({ field: `${b.name || id} · ${key === "approvers" ? "Approvers" : key.replaceAll("_", " ")}`, before: present(a[key]), after: present(b[key]), change: "Changed" });
        }
    }
    const edgeKey = (e: Graph["edges"][number]) => `${e.from} → ${e.to}${e.default ? " (default)" : ""}`;
    for (const key of new Set([...before.edges, ...after.edges].map(edgeKey))) {
        const a = before.edges.find(e => edgeKey(e) === key), b = after.edges.find(e => edgeKey(e) === key);
        if (stable(a?.when) !== stable(b?.when) || a?.default !== b?.default || !a || !b)
            rows.push({ field: `Condition / connection ${key}`, before: present(a?.when ?? a), after: present(b?.when ?? b), change: !a ? "Added" : !b ? "Removed" : "Changed" });
    }
    return rows;
}
