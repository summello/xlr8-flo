import { CheckCircle, Circle, XCircle } from "@phosphor-icons/react";
import { label, type RequisitionStatus, type StatusEvent } from "./api";

const STEPS = ["draft", "approval_pending", "sourcing_in_progress", "sourced", "awarded", "open_for_purchase", "completed"] as const satisfies readonly RequisitionStatus[];
const TERMINAL = ["rejected", "withdrawn", "cancelled", "expired"] as const;
const WORDS: Record<RequisitionStatus, string> = {
  draft: "It is a draft. Nothing is reserved until you submit it.",
  approval_pending: "It is waiting for approval. When it is approved, sourcing can begin.",
  sourcing_in_progress: "Sourcing is under way. Quotes are being gathered.",
  sourced: "Sourcing is complete. An award comes next.",
  awarded: "It is awarded. Purchase orders come next.",
  open_for_purchase: "It is open for purchase. Orders can be raised against it.",
  completed: "It is complete. Everything requested was ordered.",
  rejected: "It was rejected. Anything reserved was released once.",
  withdrawn: "It was withdrawn. Anything reserved was released once.",
  cancelled: "It was cancelled. Anything reserved was released once.",
  expired: "It expired before it was used. Anything unused was released once.",
};

/** REQ-005: the lifecycle as an ordered list with exactly one `aria-current="step"`, terminal states in words. */
export default function Stepper({ status, events }: { status: RequisitionStatus; events: StatusEvent[] | null }) {
  const terminal = (TERMINAL as readonly string[]).includes(status);
  const entry = terminal ? events?.find(e => e.to_status === status) : undefined;
  const reached = terminal ? Math.max(0, STEPS.indexOf((entry?.from_status ?? "draft") as typeof STEPS[number])) : STEPS.indexOf(status as typeof STEPS[number]);
  return <section className="req-card req-wide" aria-labelledby="where-title">
    <header className="req-card-head"><h2 id="where-title" tabIndex={-1}>Where It Is</h2></header>
    <ol className="req-stepper" aria-label="Requisition lifecycle">
      {STEPS.map((step, i) => {
        const current = !terminal && i === reached;
        const done = i < reached || (terminal && i === reached);
        return <li key={step} aria-current={current ? "step" : undefined} data-state={current ? "current" : done ? "done" : "later"}>
          {done ? <CheckCircle aria-hidden="true" weight="regular" /> : <Circle aria-hidden="true" weight="regular" />}
          <span>{label(step)}</span><span className="visually-hidden">{current ? ", current" : done ? ", done" : ", not yet"}</span></li>;
      })}
      {terminal && <li aria-current="step" data-state="current" data-terminal><XCircle aria-hidden="true" weight="regular" /><span>{label(status)}</span><span className="visually-hidden">, current</span></li>}
    </ol>
    <p>{WORDS[status]}</p>
  </section>;
}
