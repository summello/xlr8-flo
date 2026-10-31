import Money from "../../ui/Money";
import { nonZero, verdict, type Award } from "./api";

/** Scope and Funds Check: the server's figures per requisition, and what approval will release. */
export default function Checks({ award }: { award: Award }) {
  const ccy = award.currency;
  return <section className="req-card" aria-labelledby="aw-checks-h">
    <h3 id="aw-checks-h">Scope and Funds Check</h3>
    {award.checks.length === 0 ? <p className="req-sub">Award a quantity to see the check.</p> : <table className="aw-table">
      <caption className="visually-hidden">Scope, reserved funds and award amount per requisition</caption>
      <thead><tr><th scope="col">Requisition</th><th scope="col" className="numeric">Scope</th><th scope="col" className="numeric">Reserved Funds</th><th scope="col" className="numeric">Award</th><th scope="col">Result</th></tr></thead>
      <tbody>{award.checks.map(c => <tr key={c.requisition_id}>
        <th scope="row">{c.requisition}</th>
        <td className="numeric"><Money value={c.scope} currency={ccy} zero="value" /></td>
        <td className="numeric"><Money value={c.funds} currency={ccy} zero="value" /></td>
        <td className="numeric"><Money value={c.award} currency={ccy} zero="value" /></td>
        <td>{verdict([c]).kind === "ok" ? "Within scope and funds" : nonZero(c.over_scope_by) ? "Over scope" : "Over reserved funds"}</td>
      </tr>)}</tbody>
    </table>}
    {award.checks.filter(c => nonZero(c.release)).map(c => <p key={c.requisition_id} className="req-sub">On approval, <Money value={c.release} currency={ccy} zero="value" /> will be released and returned to {c.main_project || "the main project"} ({c.requisition}).</p>)}
  </section>;
}
