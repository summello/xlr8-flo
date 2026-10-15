import { useEffect, useState } from "react";
import type { components } from "../../../api/generated/schema";
import { apiClient } from "../../../api/client";
import Sheet, { Pill } from "./Sheet";
import { graphDiff, type Difference } from "../diff/graph-diff";
type Workflow = components["schemas"]["WorkflowRead"];
export default function History({ workflow, onClose }: {
    workflow: Workflow;
    onClose: () => void;
}) {
    const [row, setRow] = useState<Workflow>(), [diff, setDiff] = useState<Difference[]>([]), [error, setError] = useState(""), [loading, setLoading] = useState(true), [retry, setRetry] = useState(0);
    useEffect(() => {
        let active = true;
        void (async () => {
            await Promise.resolve();
            if (!active)
                return;
            setLoading(true);
            setError("");
            setDiff([]);
            try {
                const r = await apiClient.GET("/api/v1/workflows/{id}", { params: { path: { id: workflow.id } } });
                if (!r.data)
                    throw new Error(r.error?.detail ?? "History could not load.");
                const w = r.data;
                if (active)
                    setRow(w);
                const draft = w.versions?.find(v => v.status === "draft"), published = w.versions?.find(v => v.status === "published");
                if (draft && published) {
                    const responses = await Promise.all([published, draft].map(v => apiClient.GET("/api/v1/workflows/{id}/versions/{version}", { params: { path: { id: w.id, version: v.version } } })));
                    const [a, b] = responses;
                    if (!a?.data || !b?.data)
                        throw new Error("The comparison could not load.");
                    if (active)
                        setDiff(graphDiff(a.data.graph, b.data.graph));
                }
            }
            catch (e) {
                if (active)
                    setError(`${e instanceof Error ? e.message : "History could not load."} Workflow versions are preserved. Try again.`);
            }
            finally {
                if (active)
                    setLoading(false);
            }
        })();
        return () => { active = false; };
    }, [workflow.id, retry]);
    return <Sheet title="Version History" onClose={onClose}>{loading ? <p role="status" className="wfl-loading">Loading version history…</p> : <>{error && <p role="alert">{error}<button onClick={() => setRetry(n => n + 1)}>Try again</button></p>}<p>Published versions are read-only. Submitted documents keep their version, routing and history.</p><ol>{[...(row?.versions ?? [])].reverse().map(v => <li key={v.version}><Pill state={v.status}>{v.status === "draft" ? "Draft" : "Version"} {v.version} · {v.status}</Pill><p><time dateTime={v.published_at ?? v.created_at}>{formatDate(v.published_at ?? v.created_at)}</time> by {v.author_name ?? "Unknown"} · {v.in_flight ?? "Not available"} in flight</p></li>)}</ol>{row?.draft_version && row.published_version ? <><h3>Draft {row.draft_version} Compared With Published {row.published_version}</h3>{diff.length ? <table className="diff"><caption>Workflow changes</caption><thead><tr><th>Field</th><th>Previous</th><th>Draft</th><th>Change</th></tr></thead><tbody>{diff.map((d, i) => <tr key={i}><th scope="row">{d.field}</th><td data-label="Previous"><s>{d.before}</s></td><td data-label="Draft"><strong>{d.after}</strong></td><td data-label="Change"><Pill state="neutral">{d.change}</Pill></td></tr>)}</tbody></table> : <p>No changes between these versions.</p>}</> : <p>No published and draft pair to compare yet.</p>}</>}</Sheet>;
}

function formatDate(value: string): string {
    return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(value));
}
