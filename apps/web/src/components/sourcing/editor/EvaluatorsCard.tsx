import { MagnifyingGlass, Prohibit, Trash, UserPlus } from "@phosphor-icons/react";
import { useState } from "react";
import { apiClient } from "../../../api/client";
import type { components } from "../../../api/generated/schema";
import Person from "../../ui/Person";
import { failure, mutationHeaders, type Problem } from "../../requisitions/api";
import { useLoad } from "../../requisitions/useLoad";
import type { CardProps } from "./cards";

type Eligible = components["schemas"]["EligibleEvaluator"];
const PAGE = 20;

/** Draft and published: the people who score. Names come from `display_name` (the read has no email); the picker offers only `rfq.view` holders not already on the RFQ. */
export default function EvaluatorsCard({ rfq, report }: CardProps) {
  const [q, setQ] = useState("");
  const [found, setFound] = useState<{ q: string; rows: Eligible[]; more: boolean } | null>(null);
  const [busy, setBusy] = useState(false);
  const load = useLoad(async () => {
    const { data, error } = await apiClient.GET("/api/v1/sourcing/rfqs/{id}/evaluation", { params: { path: { id: rfq.id } } });
    if (!data) throw new Error(failure(error as Problem | undefined, "Loading the evaluators"));
    return data.evaluators ?? [];
  }, [rfq.id]);

  const head = <header className="req-card-head"><h2 id="rfq-card-evaluators">Evaluators</h2></header>;
  if (!rfq.can_edit) return null;
  if (load.error) return <section className="req-card" aria-labelledby="rfq-card-evaluators">{head}<div role="alert"><p>{load.error}</p><button type="button" onClick={load.reload}>Try again</button></div></section>;
  if (!load.data) return <section className="req-card req-card-skeleton" aria-labelledby="rfq-card-evaluators" aria-busy="true">{head}<p className="req-skeleton" role="status" aria-label="Loading evaluators" /></section>;
  const evaluators = load.data;

  const search = async (term: string, offset: number) => {
    if (term.trim().length < 2) { report("Type at least two characters to search for an evaluator."); return; }
    setBusy(true); report(null);
    try {
      const { data, error } = await apiClient.GET("/api/v1/sourcing/rfqs/{id}/evaluation/evaluators/eligible", { params: { path: { id: rfq.id }, query: { q: term.trim(), limit: PAGE, offset } } });
      if (!data) { report(failure(error as Problem | undefined, "Searching for evaluators")); return; }
      setFound({ q: term.trim(), rows: offset ? [...(found?.rows ?? []), ...data.rows] : data.rows, more: data.has_more });
    } catch { report(failure(undefined, "Searching for evaluators")); } finally { setBusy(false); }
  };

  const act = async (run: () => Promise<{ error?: unknown; response: Response }>, action: string, after?: () => void) => {
    setBusy(true); report(null);
    try {
      const { error, response } = await run();
      if (!response.ok) { report((error as Problem | undefined)?.errors?.[0]?.message ?? failure(error as Problem | undefined, action)); return; }
      after?.(); load.reload();
    } catch { report(failure(undefined, action)); } finally { setBusy(false); }
  };

  return <section className="req-card" aria-labelledby="rfq-card-evaluators" aria-busy={busy || undefined}>{head}
    {evaluators.length === 0 ? <p>No evaluators yet.</p> : <ul className="vendor-list-plain">{evaluators.map(e => <li key={e.identity_id} className="rfq-invitee">
      <strong>{e.display_name}</strong>
      {e.declaration === "conflict" && <span className="req-sub"><Prohibit aria-hidden="true" weight="regular" /> Recused{e.detail ? `: ${e.detail}` : ""}</span>}
      <div className="req-actions"><button type="button" aria-label={`Remove ${e.display_name}`} onClick={() => void act(() => apiClient.DELETE("/api/v1/sourcing/rfqs/{id}/evaluation/evaluators/{identity_id}", { params: { path: { id: rfq.id, identity_id: e.identity_id } }, headers: mutationHeaders(crypto.randomUUID(), e.version) }), "Removing the evaluator", () => setFound(null))}><Trash aria-hidden="true" weight="regular" />Remove</button></div>
    </li>)}</ul>}
    <form role="search" aria-label="Evaluator search" onSubmit={e => { e.preventDefault(); void search(q, 0); }}>
      <div className="form-field"><label htmlFor="rfq-evaluator-search">Find an Evaluator</label>
        <input id="rfq-evaluator-search" type="search" value={q} onChange={e => setQ(e.target.value)} /><p className="req-sub">Search by name or email, at least two characters. Only people who can view this RFQ are listed.</p></div>
      <div className="req-actions"><button type="submit"><MagnifyingGlass aria-hidden="true" weight="regular" />Search</button></div>
    </form>
    {found && (found.rows.length === 0 ? <p role="status">No one matches that search.</p> : <ul className="vendor-list-plain" aria-label="People who can evaluate">{found.rows.map(p => <li key={p.identity_id} className="rfq-invitee">
      <Person name={p.display_name} email={p.email} />
      <div className="req-actions"><button type="button" aria-label={`Add ${p.display_name} as an evaluator`} onClick={() => void act(() => apiClient.POST("/api/v1/sourcing/rfqs/{id}/evaluation/evaluators", { params: { path: { id: rfq.id } }, headers: mutationHeaders(crypto.randomUUID()), body: { identity_id: p.identity_id } }), "Adding the evaluator", () => setFound(f => f && { ...f, rows: f.rows.filter(r => r.identity_id !== p.identity_id) }))}><UserPlus aria-hidden="true" weight="regular" />Add</button></div>
    </li>)}</ul>)}
    {found?.more && <div className="req-actions"><button type="button" onClick={() => void search(found.q, found.rows.length)}>Show More</button></div>}
  </section>;
}
