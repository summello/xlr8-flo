import { useRef } from "react";
import { apiClient } from "../../../api/client";
import Award from "../../../components/sourcing/award/Award";
import type { Award as AwardRead } from "../../../components/sourcing/award/api";
import { isSealed, type Comparison } from "../../../components/sourcing/comparison/api";
import { failure, mutationHeaders, type Problem } from "../../../components/requisitions/api";
import { useLoad } from "../../../components/requisitions/useLoad";
import type { Rfq } from "../../../components/sourcing/api";
import "../../../styles/projects.css";
import "../../../styles/requisitions.css";
import "../../../styles/sourcing.css";
import "../../../styles/comparison.css";
import "../../../styles/award.css";

const BASES = ["lowest_cost_supplier", "best_average_cost"] as const;

/** `/sourcing/rfqs/:id/award`: load the draft, or create it from the comparison's `?basis=`. */
export default function RfqAward({ id, navigate }: { id: string; navigate: (href: string) => void }) {
  const basis = BASES.find(b => b === new URLSearchParams(window.location.search).get("basis"));
  const path = { params: { path: { id } } };
  const rfq = useLoad(async (): Promise<Rfq> => {
    const { data, error } = await apiClient.GET("/api/v1/sourcing/rfqs/{id}", path);
    if (!data) throw new Error(failure(error, "Loading the RFQ"));
    return data;
  }, [id]);
  const comparison = useLoad(async (): Promise<Comparison> => {
    const { data, error } = await apiClient.GET("/api/v1/sourcing/rfqs/{id}/comparison", path);
    if (!data || isSealed(data)) throw new Error(failure(error as Problem | undefined, "Loading the comparison"));
    return data;
  }, [id]);
  const creating = useRef<ReturnType<typeof createAward> | null>(null); // one create per mount, so a dev double-run sends one key
  const createAward = (basis: (typeof BASES)[number]) => apiClient.POST("/api/v1/sourcing/rfqs/{id}/award", { ...path, headers: mutationHeaders(crypto.randomUUID()), body: { basis } });
  const award = useLoad(async (): Promise<AwardRead | null> => {
    const first = await apiClient.GET("/api/v1/sourcing/rfqs/{id}/award", path);
    if (first.data) return first.data;
    if (first.response?.status !== 404) throw new Error(failure(first.error as Problem | undefined, "Loading the award"));
    if (!basis) return null;
    const made = await (creating.current ??= createAward(basis));
    if (made.data) return made.data;
    if (made.response?.status === 409 && (made.error as Problem | undefined)?.checks?.problem === "award_exists") { // another tab created it
      const again = await apiClient.GET("/api/v1/sourcing/rfqs/{id}/award", path);
      if (again.data) return again.data;
    }
    throw new Error(failure(made.error as Problem | undefined, "Creating the award"));
  }, [id]);

  const back = <a href={`/sourcing/rfqs/${id}/comparison`} onClick={e => { e.preventDefault(); navigate(`/sourcing/rfqs/${id}/comparison`); }}>Back to the comparison</a>;
  const failed = rfq.error ?? comparison.error ?? award.error;
  if (failed) return <section className="req-page cmp" aria-label="Award"><div role="alert"><p>{failed}</p><button type="button" onClick={() => { rfq.reload(); comparison.reload(); award.reload(); }}>Try again</button></div>{back}</section>;
  if (!rfq.data || !comparison.data || award.loading) return <section className="req-page cmp" aria-label="Award" aria-busy="true"><p className="req-skeleton" role="status" aria-label="Loading award" /></section>;
  if (!award.data) return <section className="req-page cmp" aria-label="Award"><div className="req-card"><h3>No Award Yet</h3><p>Start one from the comparison, choosing an award basis.</p>{back}</div></section>;
  return <section className="req-page cmp" aria-label="Award">
    <header className="cmp-head"><h2>Award · {rfq.data.number} · {rfq.data.title}</h2>{back}</header>
    <Award key={award.data.id} id={id} award={award.data} c={comparison.data} onAward={award.set} navigate={navigate} />
  </section>;
}
