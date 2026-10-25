import { apiClient } from "../../../api/client";
import { failure } from "../../../components/requisitions/api";
import { useLoad } from "../../../components/requisitions/useLoad";
import "../../../styles/requisitions.css";

/** ponytail: a landing page so the Bids card link opens something real; E12-S10 replaces this file with the comparison dashboard (pages/rfq-comparison.md). */
export default function RfqComparison({ id, navigate }: { id: string; navigate: (href: string) => void }) {
  const comparison = useLoad(async () => {
    const { data, error } = await apiClient.GET("/api/v1/sourcing/rfqs/{id}/comparison", { params: { path: { id } } });
    if (!data) throw new Error(failure(error, "Loading the comparison"));
    return data;
  }, [id]);
  return <section className="req-page" aria-label="Bid comparison">
    <h2>Bid Comparison</h2>
    {comparison.error ? <div role="alert"><p>{comparison.error}</p><button type="button" onClick={comparison.reload}>Try again</button></div>
      : !comparison.data ? <p className="req-skeleton req-skeleton-short" role="status" aria-label="Loading comparison" />
      : <p>The comparison dashboard is not available yet. Bids stay on record and nothing is lost.</p>}
    <a href={`/sourcing/rfqs/${id}`} onClick={e => { e.preventDefault(); navigate(`/sourcing/rfqs/${id}`); }}>Back to the RFQ</a>
  </section>;
}
