import { useRef, useState } from "react";
import { apiClient } from "../../../api/client";
import { failure, mutationHeaders, type Problem } from "../../requisitions/api";
import type { PoTransmission } from "../api";

/** Resend to the same recipients: one Idempotency-Key per attempt, cleared on success and on any 4xx; a second click while in flight is ignored. */
export function useResend(poId: string, onSent: (rows: PoTransmission[]) => void) {
  const [busy, setBusy] = useState(false), [refusal, setRefusal] = useState<string | null>(null);
  const key = useRef<string | null>(null), inFlight = useRef(false);
  const resend = async () => {
    if (inFlight.current) return;
    inFlight.current = true; setBusy(true); setRefusal(null);
    key.current ??= crypto.randomUUID();
    try {
      const { data, error, response } = await apiClient.POST("/api/v1/purchase-orders/{id}/transmissions/resend", { params: { path: { id: poId } }, headers: mutationHeaders(key.current), body: {} });
      if (data) { key.current = null; onSent(data); return; }
      if (response.status >= 400 && response.status < 500) key.current = null;
      setRefusal(failure(error as Problem | undefined, "Resending"));
    } catch { setRefusal(failure(undefined, "Resending")); }
    finally { inFlight.current = false; setBusy(false); }
  };
  return { resend, busy, refusal, clearRefusal: () => setRefusal(null) };
}
