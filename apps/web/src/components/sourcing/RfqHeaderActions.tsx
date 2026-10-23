import ExtendDeadlineDialog from "./ExtendDeadlineDialog";
import type { Rfq } from "./api";

/** The detail header's actions, by state and by what the server says the caller may do. Later stories add theirs here (Edit, Amend, Cancel RFQ, Open Comparison, View Award). */
export default function RfqHeaderActions({ rfq, onChanged }: { rfq: Rfq; onChanged: (next: Rfq) => void }) {
  const actions = [rfq.status === "published" && rfq.can_edit ? <ExtendDeadlineDialog key="extend" rfq={rfq} onDone={onChanged} /> : null].filter(Boolean);
  return actions.length === 0 ? null : <div className="req-actions" role="group" aria-label="RFQ actions">{actions}</div>;
}
