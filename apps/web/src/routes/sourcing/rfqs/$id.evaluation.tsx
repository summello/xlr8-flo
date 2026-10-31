import { Prohibit } from "@phosphor-icons/react";
import { useEffect, useState } from "react";
import { apiClient } from "../../../api/client";
import { failure, formatDateTime } from "../../../components/requisitions/api";
import { useLoad } from "../../../components/requisitions/useLoad";
import { loadEvaluation, RECUSED } from "../../../components/sourcing/evaluation/api";
import Declaration from "../../../components/sourcing/evaluation/Declaration";
import Scoring from "../../../components/sourcing/evaluation/Scoring";
import "../../../styles/requisitions.css";
import "../../../styles/sourcing.css";

/** The deadline in the viewer's locale, from the server's own instant; read only while scoring is still closed. */
function Opens({ id }: { id: string }) {
  const rfq = useLoad(async () => {
    const { data, error } = await apiClient.GET("/api/v1/sourcing/rfqs/{id}", { params: { path: { id } } });
    if (!data) throw new Error(failure(error, "Loading the deadline"));
    return data;
  }, [id]);
  return <p role="status">Scoring Opens at the Deadline{rfq.data ? `: ${formatDateTime(rfq.data.deadline_at)} (${rfq.data.deadline_tz ?? "UTC"}).` : "."} Until then no bid content is shown to anyone.{rfq.error && <> {rfq.error}</>}</p>;
}

/** SRC-011: declare first, then score. The page mirrors the server and adds no rule. */
export default function RfqEvaluation({ id, navigate }: { id: string; navigate: (href: string) => void }) {
  const load = useLoad(() => loadEvaluation(id), [id]);
  const [notice, setNotice] = useState<string | null>(null);
  useEffect(() => { document.title = "Evaluation"; }, []);
  const back = <a href={`/sourcing/rfqs/${id}`} onClick={e => { e.preventDefault(); navigate(`/sourcing/rfqs/${id}`); }}>Back to the RFQ</a>;
  const view = load.data;
  const changed = (text: string) => { setNotice(text); load.reload(); };
  return <section className="req-page eval-page" aria-label="Evaluation">
    {load.error ? <div role="alert"><p>{load.error}</p><button type="button" onClick={load.reload}>Try again</button></div>
      : !view ? <p className="req-skeleton req-skeleton-short" role="status" aria-label="Loading evaluation" />
      : view === RECUSED ? <p className="rfq-banner" role="status"><Prohibit aria-hidden="true" weight="regular" /><span>You are recused from this RFQ. The RFQ owner can see your declaration.</span></p>
      : !view.me ? <p>You are not an evaluator on this RFQ</p>
      : view.me.declaration === "none" ? <Declaration id={id} onDone={() => changed("Declaration saved.")} />
      : <>
        {notice && <p role="status">{notice}</p>}
        {view.scoring_open ? <Scoring key={view.me.version} id={id} evaluation={view} me={view.me} onChanged={changed} />
          : view.status === "awarded" || view.status === "cancelled" ? <p role="status">This RFQ is closed to scoring.</p>
          : <Opens id={id} />}
        <Declaration id={id} known onDone={() => changed("Conflict declared.")} />
      </>}
    {back}
  </section>;
}
