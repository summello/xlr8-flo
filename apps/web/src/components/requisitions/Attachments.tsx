import { CheckCircle, Prohibit, Spinner, UploadSimple } from "@phosphor-icons/react";
import { useRef, useState } from "react";
import { apiClient } from "../../api/client";
import { writeHeaders } from "../../features/auth/api";
import { failure, type Attachment } from "./api";
import { useLoad } from "./useLoad";

const SCAN = {
  clean: { word: "Clean", Icon: CheckCircle, why: null },
  pending: { word: "Scanning", Icon: Spinner, why: "It is still being scanned. You can open it once the scan is clean." },
  basic_checks_passed: { word: "Scanning", Icon: Spinner, why: "It is still being scanned. You can open it once the scan is clean." },
  quarantined: { word: "Quarantined", Icon: Prohibit, why: "It was quarantined by the scan and cannot be opened." },
  rejected: { word: "Quarantined", Icon: Prohibit, why: "It was rejected by the scan and cannot be opened." },
} as const;

export default function Attachments({ id }: { id: string | null }) {
  const list = useLoad<Attachment[]>(async () => {
    if (!id) return [];
    const { data, error } = await apiClient.GET("/api/v1/documents/{document_type}/{document_id}/attachments", { params: { path: { document_type: "requisition", document_id: id } } });
    if (!data) throw new Error(failure(error, "Loading attachments"));
    return data;
  }, [id]);
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const upload = async (file: File) => {
    if (!id) return;
    setBusy(true); setProblem(null);
    try {
      const body = new FormData(); body.set("file", file);
      // No Content-Type: the browser sets the multipart boundary.
      const response = await fetch(`/api/v1/documents/requisition/${id}/attachments`, { method: "POST", credentials: "same-origin", headers: writeHeaders(), body });
      if (!response.ok) { const error = await response.json().catch(() => undefined) as { detail?: string } | undefined; setProblem(failure(error, "Uploading the file")); }
      else list.reload();
    } catch { setProblem(failure(undefined, "Uploading the file")); }
    setBusy(false);
  };
  return <section className="req-card" aria-labelledby="attachments-title">
    <header className="req-card-head"><h2 id="attachments-title">Attachments</h2></header>
    {!id ? <p>Save the draft to attach files</p> : <>
      <input ref={input} type="file" className="visually-hidden" tabIndex={-1} aria-label="Choose a file to upload" onChange={e => { const f = e.target.files?.[0]; if (f) void upload(f); e.target.value = ""; }} />
      <button type="button" aria-busy={busy || undefined} onClick={() => input.current?.click()}><UploadSimple aria-hidden="true" weight="regular" />{busy ? "Uploading" : "Upload file"}</button>
      {problem && <p role="alert" className="field-error">{problem}</p>}
      {list.error ? <div role="alert"><p>{list.error}</p><button type="button" onClick={list.reload}>Try again</button></div> :
        list.loading && !list.data ? <p className="req-skeleton req-skeleton-short" role="status" aria-label="Loading files" /> :
        (list.data ?? []).length === 0 ? <p>No files yet. Quotes and drawings help the approver. Each file is scanned before anyone can open it.</p> :
        <><ul className="req-files" aria-busy={list.loading || undefined}>{(list.data ?? []).map(file => {
          const scan = SCAN[file.scan_status]; const Icon = scan.Icon;
          return <li key={file.id}>
            <div>{scan.why === null ? <a href={`/api/v1/attachments/${file.id}/content`}>{file.display_name}</a> : <span>{file.display_name}</span>}
              <span>{file.media_type.split("/").pop()?.toUpperCase()} · {new Intl.NumberFormat(undefined, { maximumFractionDigits: 0 }).format(Math.max(1, file.size_bytes / 1024))} KB · {file.uploaded_by_email}</span>
              {scan.why && <span>{scan.why}</span>}</div>
            <span className="req-pill" data-tone={scan.word === "Clean" ? "success" : scan.word === "Scanning" ? "info" : "danger"}><Icon aria-hidden="true" weight="regular" />{scan.word}</span></li>;
        })}</ul>{list.loading && <p role="status" className="req-refreshing">Refreshing files…</p>}</>}
    </>}
  </section>;
}
