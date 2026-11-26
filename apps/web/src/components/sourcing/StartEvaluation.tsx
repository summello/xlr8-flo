import { ClipboardText } from "@phosphor-icons/react";
import { useRef, useState } from "react";
import { apiClient } from "../../api/client";
import { failure, mutationHeaders, type Problem } from "../requisitions/api";
import type { Rfq } from "./api";

/** closed -> under_evaluation, then on to the evaluation screen. Not `disabled` while pending: a disabled button drops keyboard focus. */
export default function StartEvaluation({ rfq, navigate }: { rfq: Rfq; navigate: (href: string) => void }) {
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  // One key until a response arrives: a retry after a lost response replays the stored outcome.
  const key = useRef<string | null>(null), inFlight = useRef(false);
  const start = async () => {
    if (inFlight.current) return;
    inFlight.current = true; setBusy(true); setMessage(null);
    key.current ??= crypto.randomUUID();
    try {
      const { data, error } = await apiClient.POST("/api/v1/sourcing/rfqs/{id}/start-evaluation", { params: { path: { id: rfq.id } }, headers: mutationHeaders(key.current) });
      key.current = null; // a response arrived, so the outcome is known
      if (data) { navigate(`/sourcing/rfqs/${rfq.id}/evaluation`); return; }
      setMessage(failure(error as Problem | undefined, "Starting evaluation"));
    } catch { setMessage(failure(undefined, "Starting evaluation")); } // no response: the key is kept, so a retry replays
    finally { inFlight.current = false; setBusy(false); }
  };
  return <>
    <button type="button" aria-busy={busy || undefined} onClick={start}><ClipboardText aria-hidden="true" weight="regular" />{busy ? "Starting…" : "Start Evaluation"}</button>
    {message && <p role="alert" className="field-error">{message}</p>}
  </>;
}
