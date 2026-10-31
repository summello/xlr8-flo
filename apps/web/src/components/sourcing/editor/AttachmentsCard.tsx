import { Trash, UploadSimple } from "@phosphor-icons/react";
import { useRef, useState } from "react";
import { apiClient } from "../../../api/client";
import { writeHeaders } from "../../../features/auth/api";
import { failure } from "../../requisitions/api";
import type { CardProps } from "./cards";
import { fetchRfq, write } from "./api";

/** Every file is `Internal` until someone chooses to share it. Draft only: after publication the list is read-only. */
export default function AttachmentsCard({ rfq, mode, onRfq, report }: CardProps) {
  const [busy, setBusy] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const files = rfq.attachments ?? [];

  const refresh = async () => { try { onRfq(await fetchRfq(rfq.id)); } catch (e) { report(e instanceof Error ? e.message : failure(undefined, "Refreshing the files")); } };
  const upload = async (file: File) => {
    setBusy(true); report(null);
    try {
      const body = new FormData(); body.set("file", file);
      const response = await fetch(`/api/v1/documents/rfq/${rfq.id}/attachments`, { method: "POST", credentials: "same-origin", headers: writeHeaders(), body }); // no Content-Type: the browser sets the boundary
      if (!response.ok) report(failure(await response.json().catch(() => undefined) as { detail?: string } | undefined, "Uploading the file")); else await refresh();
    } catch { report(failure(undefined, "Uploading the file")); }
    setBusy(false);
  };
  const remove = async (id: string) => {
    setBusy(true); report(null);
    try {
      const response = await apiClient.DELETE("/api/v1/attachments/{id}", { params: { path: { id } }, headers: writeHeaders() });
      if (response.response.ok) await refresh(); else report(failure(response.error as { detail?: string } | undefined, "Removing the file"));
    } catch { report(failure(undefined, "Removing the file")); }
    setBusy(false);
  };
  const share = async (id: string, vendor_visible: boolean) => {
    setBusy(true); report(null);
    const done = await write(h => apiClient.PUT("/api/v1/sourcing/rfqs/{id}/attachments/{aid}/visibility", { params: { path: { id: rfq.id, aid: id } }, headers: h, body: { vendor_visible } }), rfq, "Changing who can see the file");
    setBusy(false);
    if (done.rfq) onRfq(done.rfq); else report(done.message);
  };

  return <section className="req-card" aria-labelledby="rfq-card-attachments" aria-busy={busy || undefined}>
    <header className="req-card-head"><h2 id="rfq-card-attachments">Attachments</h2></header>
    {mode === "edit" && <>
      <input ref={input} type="file" className="visually-hidden" tabIndex={-1} aria-label="Choose a file to upload" onChange={e => { const f = e.target.files?.[0]; if (f) void upload(f); e.target.value = ""; }} />
      <button type="button" aria-busy={busy || undefined} onClick={() => input.current?.click()}><UploadSimple aria-hidden="true" weight="regular" />{busy ? "Working" : "Upload File"}</button>
    </>}
    {files.length === 0 ? <p>No files yet. Attachments are optional; each is scanned and stays internal until you share it.</p> : <ul className="req-files">{files.map(f => <li key={f.id}>
      <div><span>{f.display_name}</span><span className="req-sub">{f.scan_status === "clean" ? "Scan clean" : `Scan ${f.scan_status.replaceAll("_", " ")}`} · {f.vendor_visible ? "Shared with vendors" : "Internal"}</span></div>
      {mode === "edit" && <div className="req-actions">
        <label><input type="checkbox" checked={f.vendor_visible} onChange={e => void share(f.id, e.target.checked)} /> Share {f.display_name} with vendors</label>
        <button type="button" aria-label={`Remove ${f.display_name}`} onClick={() => void remove(f.id)}><Trash aria-hidden="true" weight="regular" />Remove</button>
      </div>}
    </li>)}</ul>}
  </section>;
}
