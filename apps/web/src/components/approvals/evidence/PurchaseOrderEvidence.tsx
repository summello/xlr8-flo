import type { components } from "../../../api/generated/schema";
import Eligibility from "../../vendors/Eligibility";
import Money from "../../ui/Money";
import Person from "../../ui/Person";

/** The server's evidence for a purchase order (purchasing module). Rendered as given; the browser computes nothing. */
type Json = Record<string, unknown>;
type Effect = { project_id: string; project_number: string; project_name: string; currency: string; amount: string };
type Line = { line_no: number; vendor_name: string; justification?: string | null };
type Recipient = { name: string; email: string };

const arr = <T,>(value: unknown): T[] => (Array.isArray(value) ? (value as T[]) : []);
const BASIS: Record<string, string> = { lowest_cost_supplier: "Lowest Cost Supplier", best_average_cost: "Best Average Cost", manual: "Manual Selection" };

export default function PurchaseOrderEvidence({ evidence }: { evidence: Json }) {
  const award = (evidence.award ?? {}) as Json;
  const effects = arr<Effect>(evidence.budget_effect);
  const eligibility = evidence.vendor_eligibility_now as components["schemas"]["Eligibility"] | null;
  const notLowest = arr<Line>(award.not_lowest);
  const recipients = arr<Recipient>(evidence.recipients);
  const rationale = (award.rationale as string | null) || undefined;
  return (
    <section aria-labelledby="appr-po-evidence" className="appr-evidence">
      <h3 id="appr-po-evidence">Purchase Order Evidence</h3>
      <dl className="appr-facts">
        <div><dt>Award basis</dt><dd>{BASIS[String(award.basis)] ?? String(award.basis ?? "Not recorded")}</dd></div>
        {rationale && <div><dt>Rationale</dt><dd>{rationale}</dd></div>}
        <div><dt>Vendor eligibility now</dt><dd>{eligibility ? <Eligibility eligibility={eligibility} variant="field" /> : "Not available"}</dd></div>
      </dl>

      <div className="appr-scroll" role="region" aria-label="Budget Effect When Issued" tabIndex={0}>
        <table className="appr-evidence-table">
          <caption>Budget Effect When Issued</caption>
          <thead><tr><th scope="col">Project</th><th scope="col" className="appr-num">Amount</th></tr></thead>
          <tbody>
            {effects.map((e) => (
              <tr key={e.project_id}>
                <th scope="row">{e.project_number} {e.project_name}</th>
                <td className="appr-num"><Money value={e.amount} currency={e.currency} zero="value" /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {notLowest.length > 0 && (
        <section aria-labelledby="appr-po-notlowest">
          <h4 id="appr-po-notlowest">Award Justification</h4>
          <ul className="appr-evidence-docs">
            {notLowest.map((l) => <li key={`${l.line_no}-${l.vendor_name}`}><strong>Line {l.line_no}, {l.vendor_name}</strong>: {l.justification || "No justification given"}</li>)}
          </ul>
        </section>
      )}

      <section aria-labelledby="appr-po-sent">
        <h4 id="appr-po-sent">What Will Be Sent</h4>
        {recipients.length > 0 ? (
          <ul className="appr-evidence-docs" aria-label="Recipients">
            {recipients.map((r) => <li key={r.email}><Person name={r.name} email={r.email} /></li>)}
          </ul>
        ) : <p className="appr-meta">No vendor contact receives documents.</p>}
      </section>
    </section>
  );
}
