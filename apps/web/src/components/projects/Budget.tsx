import { useCallback, useEffect, useState } from "react";
import { apiClient } from "../../api/client";
import { consumptionPercent } from "../../lib/money";
import Money from "../ui/Money";
import { failure, label, type Balance } from "./api";
export default function Budget({ id }: { id: string }) {
  const [balance, setBalance] = useState<Balance | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(async () => {
    try {
      const result = await apiClient.GET("/api/v1/projects/{project_id}/balance", { params: { path: { project_id: id } } });
      setError(null);
      if (result.data) setBalance(result.data); else setError(failure(result.error, "Loading budget"));
    } catch { setError(failure(undefined, "Loading budget")); }
    finally { setLoading(false); }
  }, [id]);
  // load updates state only after network responses; this effect starts the request.
  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => { void load(); }, [load]);
  return <section aria-label="Project budget">
    {loading && <p role="status">Loading budget… Previously loaded figures are preserved.</p>}
    {error && <div role="alert"><p>{error}</p><button onClick={() => { setLoading(true); void load(); }}>Retry budget</button></div>}
    {balance && <><p>As of {balance.as_of}</p><dl className="project-facts">
      {(["allocated", "reserved", "committed", "actual", "available"] as const).map(key => <div key={key}><dt>{label(key)}</dt><dd><Money value={balance[key]} currency={balance.currency} /></dd></div>)}
      <div><dt>Consumption</dt><dd className="numeric">{consumptionPercent(balance.allocated, balance.available)}</dd></div>
    </dl>{balance.allocated === "0.0000" && <p>No budget allocated yet.</p>}</>}
    <p><a href={`/projects/${id}/dashboard`}>Open project dashboard</a></p>
  </section>;
}
