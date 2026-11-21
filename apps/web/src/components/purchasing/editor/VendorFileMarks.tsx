import { apiClient } from "../../../api/client";
import { writeHeaders } from "../../../features/auth/api";
import { failure, type Problem } from "../../requisitions/api";
import { useLoad } from "../../requisitions/useLoad";
import Card from "../../sourcing/Card";
import { useState } from "react";

/** Which files go to the vendor (PO-017). Internal by default; a marked file waits for scanning (decision 171). */
export default function VendorFileMarks({ id, refreshKey }: { id: string; refreshKey: number }) {
  const load = useLoad(async () => {
    const { data, error } = await apiClient.GET("/api/v1/purchase-orders/{id}/attachments", { params: { path: { id } } });
    if (!data) throw new Error(failure(error as Problem | undefined, "Loading the file marks"));
    return data;
  }, [id, refreshKey]);
  const [problem, setProblem] = useState<string | null>(null);
  const mark = async (aid: string, vendor_facing: boolean) => {
    setProblem(null);
    const { error, response } = await apiClient.PUT("/api/v1/purchase-orders/{id}/attachments/{aid}/vendor-facing", { params: { path: { id, aid } }, headers: writeHeaders(), body: { vendor_facing } });
    if (!response.ok) setProblem(failure(error as Problem | undefined, "Marking the file"));
    else load.reload();
  };
  const files = load.data ?? [];
  return <Card title="Files for the Vendor" load={load}>
    {files.length === 0 ? <p>No files yet. Files are internal unless you mark them For the vendor.</p> : <ul className="vendor-list-plain">{files.map(f => <li key={f.id}>
      <label><input type="checkbox" checked={f.vendor_facing} onChange={e => void mark(f.id, e.target.checked)} />For the vendor <span className="req-sub">{f.display_name}</span></label>
      <span className="req-sub">{f.vendor_facing ? "Not sent until scanning is available" : "Internal"}</span></li>)}</ul>}
    {problem && <p role="alert" className="field-error">{problem}</p>}
  </Card>;
}
