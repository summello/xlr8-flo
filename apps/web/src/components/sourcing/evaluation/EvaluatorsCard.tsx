import { CheckCircle, Circle, Prohibit } from "@phosphor-icons/react";
import { useLoad } from "../../requisitions/useLoad";
import Card from "../Card";
import { loadEvaluation, RECUSED } from "./api";

/** The Evaluators card: the count to anyone who can see the RFQ, each evaluator's declaration and state to the RFQ's editors (the server decides who gets the list). */
export default function EvaluatorsCard({ id, navigate }: { id: string; navigate: (href: string) => void }) {
  const load = useLoad(() => loadEvaluation(id), [id]);
  const view = load.data === RECUSED ? null : load.data;
  const href = `/sourcing/rfqs/${id}/evaluation`;
  return <Card title="Evaluators" load={load}>
    {view && <p>{view.evaluator_count} {view.evaluator_count === 1 ? "evaluator" : "evaluators"} (not recused).</p>}
    {view?.evaluators && (view.evaluators.length === 0 ? <p className="req-sub">No evaluator has been added yet.</p> : <ul className="vendor-list-plain">{view.evaluators.map(e => <li key={e.identity_id}>
      <strong>{e.display_name}</strong>
      <span className="req-sub eval-state">{e.declaration === "conflict" ? <><Prohibit aria-hidden="true" weight="regular" />Recused</> : e.declaration === "clear" ? <><CheckCircle aria-hidden="true" weight="regular" />No conflict declared</> : <><Circle aria-hidden="true" weight="regular" />Not declared yet</>}</span>
      <span className="req-sub">{e.submitted ? "Scores submitted" : "Scores not submitted"}</span>
    </li>)}</ul>)}
    {load.data === RECUSED && <p className="req-sub">You are recused from this RFQ.</p>}
    {(load.data === RECUSED || view?.me) && <p><a href={href} onClick={e => { e.preventDefault(); navigate(href); }}>Open Evaluation</a></p>}
  </Card>;
}
