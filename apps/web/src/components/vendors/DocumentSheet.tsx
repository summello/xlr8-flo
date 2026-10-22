import * as Dialog from "@radix-ui/react-dialog";
import { X } from "@phosphor-icons/react";
import { useState } from "react";
import { apiClient } from "../../api/client";
import { restoreFocus } from "../../lib/focus";
import { failure, formatDate, formatDateTime, mutationHeaders } from "../requisitions/api";
import { useLoad } from "../requisitions/useLoad";
import Signal from "../ui/Signal";
import { docWords, type DocState } from "./api";

const SCAN = {
  clean: { word: "Scan Clean", tone: "success" },
  pending: { word: "Scanning", tone: "info" },
  basic_checks_passed: { word: "Scanning", tone: "info" },
  quarantined: { word: "Quarantined", tone: "danger" },
  rejected: { word: "Quarantined", tone: "danger" },
} as const;

type Props = { vendorId: string; doc: DocState; opener: HTMLElement | null; onClose: () => void; onChanged: () => void };

/** §3.6 document sheet. Radix traps focus, closes on Escape and returns focus to the opener. */
export default function DocumentSheet({ vendorId, doc, opener, onClose, onChanged }: Props) {
  const [reason, setReason] = useState("");
  const [rejecting, setRejecting] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const file = useLoad(async () => {
    if (!doc.attachment_id) return null;
    const { data } = await apiClient.GET("/api/v1/documents/{document_type}/{document_id}/attachments", { params: { path: { document_type: "vendor", document_id: vendorId } } });
    return data?.find(a => a.id === doc.attachment_id) ?? null;
  }, [vendorId, doc.attachment_id]);
  const words = docWords(doc.state, doc.days_left);
  const missing = doc.state === "missing";
  const scan = file.data ? SCAN[file.data.scan_status as keyof typeof SCAN] : undefined;

  const decide = async (kind: "verify" | "reject") => {
    if (kind === "reject" && !reason.trim()) { setProblem("Give a reason. The uploader sees it."); return; }
    setBusy(true); setProblem(null);
    const params = { path: { id: vendorId, did: doc.document_id! } };
    const headers = mutationHeaders(crypto.randomUUID());
    const result = kind === "verify"
      ? await apiClient.POST("/api/v1/vendors/{id}/documents/{did}/verify", { params, headers }).catch(() => undefined)
      : await apiClient.POST("/api/v1/vendors/{id}/documents/{did}/reject", { params, headers, body: { reason: reason.trim() } }).catch(() => undefined);
    setBusy(false);
    if (result?.data) { onChanged(); onClose(); return; }
    // The server decides who may act; a refusal is shown in words and the input is kept (UX-005).
    setProblem(result?.response.status === 403 ? "You do not have permission to review documents. Nothing was changed." : failure(result?.error as { detail?: string } | undefined, kind === "verify" ? "Verifying" : "Rejecting"));
  };

  return <Dialog.Root open onOpenChange={open => { if (!open) onClose(); }}>
    <Dialog.Portal>
      <Dialog.Overlay className="appr-scrim" />
      <Dialog.Content className="appr-sheet vendor-sheet" aria-describedby="doc-sheet-desc"
        onCloseAutoFocus={event => { event.preventDefault(); restoreFocus(opener); }}>
        <header className="appr-sheet-head">
          <div><Dialog.Title>{doc.type_code}</Dialog.Title>
            <Dialog.Description id="doc-sheet-desc" className="visually-hidden">Document details and review.</Dialog.Description></div>
          <Dialog.Close className="appr-close" aria-label="Close"><X aria-hidden="true" />Close</Dialog.Close>
        </header>
        <Signal tone={words.tone} label={words.text} />
        {missing ? <p>This required document has not been supplied. Until it is, the vendor is not eligible anywhere it is required.</p> : <dl className="vendor-facts">
          <dt>File</dt><dd>{file.loading && !file.data ? "Loading…" : file.data ? file.data.display_name : "Not available"}</dd>
          <dt>Scan</dt><dd>{scan ? <Signal tone={scan.tone} label={scan.word} /> : "—"}</dd>
          <dt>Issued</dt><dd>{formatDate(doc.issue_date)}</dd>
          <dt>Expires</dt><dd>{formatDate(doc.expiry_date)}</dd>
          <dt>Verification</dt><dd>{doc.verification ?? "—"}{doc.rejection_reason ? `: ${doc.rejection_reason}` : ""}</dd>
          <dt>Reviewed</dt><dd>{doc.reviewed_at ? formatDateTime(doc.reviewed_at) : "Not yet"}</dd>
          <dt>Versions</dt><dd>{file.data && file.data.versions > 1 ? `Version ${doc.attachment_version ?? file.data.version} of ${file.data.versions}. Earlier versions are kept.` : "Only one version."}</dd>
        </dl>}
        {problem && <p role="alert" className="field-error">{problem}</p>}
        {!missing && doc.document_id && <footer className="appr-actions">
          {rejecting ? <>
            <label>Reason, shown to the uploader<textarea value={reason} onChange={e => setReason(e.target.value)} /></label>
            <button type="button" disabled={busy} onClick={() => void decide("reject")}>Reject Document</button>
            <button type="button" onClick={() => { setRejecting(false); setProblem(null); }}>Cancel</button>
          </> : <>
            <button type="button" disabled={busy} onClick={() => void decide("verify")}>Verify</button>
            <button type="button" onClick={() => setRejecting(true)}>Reject</button>
          </>}
        </footer>}
      </Dialog.Content>
    </Dialog.Portal>
  </Dialog.Root>;
}
