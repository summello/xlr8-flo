import Signal from "../../ui/Signal";

/** The server's evidence for a vendor change (vendors module, masked before it leaves). Rendered as given. */
type Json = Record<string, unknown>;
type Line = { prompt: string; weight: number; points: number; contribution: string };
type Doc = { type_code: string; required: boolean; state: string; verification?: string | null };
type Match = { field: string; vendor_id: string; vendor_number: string; trading_name: string; masked_value: string };

const arr = <T,>(value: unknown): T[] => (Array.isArray(value) ? (value as T[]) : []);
const units = (list: unknown) => { const codes = arr<string>(list); return codes.length ? codes.join(", ") : "none"; };

export default function VendorEvidence({ evidence }: { evidence: Json }) {
  const kind = String(evidence.kind);
  const payload = (evidence.payload ?? {}) as Json;
  const score = payload.score as number | undefined;
  const threshold = payload.threshold as number | undefined;
  const matches = arr<Match>(evidence.duplicate_matches);
  const docs = arr<Doc>(evidence.document_state);
  const reason = (evidence.reason as string | null) || undefined;
  return (
    <section aria-labelledby="appr-evidence" className="appr-evidence">
      <h3 id="appr-evidence">{kind === "create" ? "Onboarding Evidence" : "Change Evidence"}</h3>
      {kind === "create" && score !== undefined && threshold !== undefined && (
        <Signal
          tone={score < threshold ? "warning" : "success"}
          label={`Score ${score} of 100, ${score < threshold ? "below" : "at or above"} the threshold of ${threshold}`}
        />
      )}
      {kind === "create" && arr<Line>(payload.breakdown).length > 0 && (
        <details className="appr-fold">
          <summary>Score Breakdown</summary>
          <table className="appr-evidence-table">
            <caption className="visually-hidden">Score by question</caption>
            <thead><tr><th scope="col">Question</th><th scope="col">Weight</th><th scope="col">Points</th><th scope="col">Contribution</th></tr></thead>
            <tbody>{arr<Line>(payload.breakdown).map((l) => <tr key={l.prompt}><th scope="row">{l.prompt}</th><td>{l.weight}</td><td>{l.points}</td><td>{l.contribution}</td></tr>)}</tbody>
          </table>
        </details>
      )}
      <dl className="appr-facts">
        {reason && <div><dt>Reason</dt><dd>{reason}</dd></div>}
        {kind === "create" && matches.map((m) => (
          <div key={m.field}><dt>Duplicate {m.field.replace("identifiers.", "")}</dt>
            <dd><a className="appr-link" href={`/vendors/${m.vendor_id}`}>{m.vendor_number} {m.trading_name}</a> <span className="appr-meta">{m.masked_value}</span></dd></div>
        ))}
        {kind === "create" && typeof payload.override_reason === "string" && <div><dt>Override reason</dt><dd>{payload.override_reason}</dd></div>}
        {kind === "availability" && <>
          <div><dt>Now</dt><dd>{String(evidence.current_availability)}, units: {units(evidence.current_units)}</dd></div>
          <div><dt>Requested</dt><dd>{String(payload.availability)}, units: {units(evidence.new_units)}</dd></div>
        </>}
        {kind !== "create" && <>
          <div><dt>Open RFQs</dt><dd>{String(evidence.open_rfqs ?? 0)}</dd></div>
          <div><dt>Issued purchase orders</dt><dd>{String(evidence.issued_purchase_orders ?? 0)}</dd></div>
        </>}
      </dl>
      {kind === "create" && docs.length > 0 && (
        <ul className="appr-evidence-docs" aria-label="Documents">
          {docs.map((d, i) => (
            <li key={`${d.type_code}-${i}`}>
              <strong>{d.type_code}</strong>: {d.state === "missing" ? (d.required ? "Missing, required" : "Missing") : `${d.state}${d.verification ? `, ${d.verification}` : ""}`}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
