import { apiClient } from "../../api/client";
import RequisitionForm from "../../components/requisitions/RequisitionForm";
import { failure } from "../../components/requisitions/api";
import { CardSkeleton } from "../../components/requisitions/DetailCards";
import { useLoad } from "../../components/requisitions/useLoad";
import "../../styles/projects.css";
import "../../styles/requisitions.css";

export default function EditRequisition({ id, navigate, onLoaded }: { id: string; navigate: (href: string) => void; onLoaded?: (title: string) => void }) {
  const loaded = useLoad(async () => {
    const [row, lines] = await Promise.all([
      apiClient.GET("/api/v1/requisitions/{id}", { params: { path: { id } } }),
      apiClient.GET("/api/v1/requisitions/{id}/lines", { params: { path: { id } } }),
    ]);
    if (!row.data) throw new Error(failure(row.error, "Loading this requisition"));
    if (!lines.data) throw new Error(failure(lines.error, "Loading the lines"));
    onLoaded?.(row.data.title);
    return { row: row.data, lines: lines.data.rows };
  }, [id]);
  if (loaded.error) return <section className="req-page" role="alert"><h2>Could not load this requisition</h2><p>{loaded.error}</p><button type="button" onClick={loaded.reload}>Try again</button></section>;
  if (!loaded.data) return <section className="req-page" aria-busy="true" aria-label="Requisition form, loading">
    <div className="req-grid"><div className="req-main"><CardSkeleton name="the details" /><CardSkeleton name="the lines" /></div>
      <aside className="req-side"><CardSkeleton name="the funding check" /><CardSkeleton name="the catalogue" /><CardSkeleton name="the attachments" /></aside></div></section>;
  if (loaded.data.row.status !== "draft") return <section className="req-page"><h2>{loaded.data.row.title}</h2>
    <p>Only a draft can be edited. A submitted requisition is changed through return, amend and resubmit.</p>
    <a href={`/requisitions/${id}`} onClick={e => { e.preventDefault(); navigate(`/requisitions/${id}`); }}>Back to the requisition</a></section>;
  return <RequisitionForm key={id} navigate={navigate} initial={loaded.data} />;
}
