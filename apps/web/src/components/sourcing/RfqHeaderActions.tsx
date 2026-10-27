import { PencilSimple, Scales } from "@phosphor-icons/react";
import ExtendDeadlineDialog from "./ExtendDeadlineDialog";
import type { Rfq } from "./api";

/** The detail header's actions, by state and by what the server says the caller may do. Later stories add theirs here (Cancel RFQ, Open Comparison, View Award). */
export default function RfqHeaderActions({ rfq, onChanged, navigate }: { rfq: Rfq; onChanged: (next: Rfq) => void; navigate: (href: string) => void }) {
  const go = (verb: "edit" | "amend", text: string) => <a key={verb} className="req-link-button" href={`/sourcing/rfqs/${rfq.id}/${verb}`} onClick={e => { e.preventDefault(); navigate(`/sourcing/rfqs/${rfq.id}/${verb}`); }}><PencilSimple aria-hidden="true" weight="regular" />{text}</a>;
  const compare = <a key="compare" className="req-link-button" href={`/sourcing/rfqs/${rfq.id}/comparison`} onClick={e => { e.preventDefault(); navigate(`/sourcing/rfqs/${rfq.id}/comparison`); }}><Scales aria-hidden="true" weight="regular" />Open Comparison</a>;
  const actions = [
    rfq.status === "draft" && rfq.can_edit ? go("edit", "Edit") : null,
    rfq.status === "published" && rfq.can_edit ? <ExtendDeadlineDialog key="extend" rfq={rfq} onDone={onChanged} /> : null,
    rfq.status === "published" && rfq.can_edit ? go("amend", "Amend") : null,
    rfq.status === "closed" || rfq.status === "under_evaluation" ? compare : null,
  ].filter(Boolean);
  return actions.length === 0 ? null : <div className="req-actions" role="group" aria-label="RFQ actions">{actions}</div>;
}
