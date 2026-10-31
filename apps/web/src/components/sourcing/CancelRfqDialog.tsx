import { Prohibit } from "@phosphor-icons/react";
import { apiClient } from "../../api/client";
import { failure, mutationHeaders, type Problem } from "../requisitions/api";
import ReasonDialog from "./ReasonDialog";
import type { Rfq } from "./api";

const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;

/** SRC-017, decision 212: cancelling keeps the whole history and moves no money. */
export default function CancelRfqDialog({ rfq, onDone }: { rfq: Rfq; onDone: (next: Rfq) => void }) {
  const lines = plural(rfq.lines.length, "line");
  const invitees = rfq.invitees?.length ?? 0, bids = rfq.bids_received ?? 0;
  return <ReasonDialog trigger={{ label: "Cancel RFQ", icon: Prohibit }} title="Cancel RFQ" keep="Keep RFQ" confirm="Cancel RFQ" busy="Cancelling…"
    intro={<>{rfq.number} will be closed to bids. It cannot be reopened, but it stays readable with its full history.</>}
    rows={[
      <><strong>Invitees.</strong> {plural(invitees, "invitee")} {invitees === 1 ? "is" : "are"} told it is cancelled and can no longer bid.</>,
      <><strong>Bids.</strong> {plural(bids, "submitted bid")} {bids === 1 ? "is" : "are"} kept on record and not evaluated.</>,
      <><strong>Requisition quantities.</strong> The {lines} on this RFQ return to the approved-lines pool and can be sourced again.</>,
      <><strong>Budget.</strong> No money moves. Each requisition's reservation is kept.</>,
    ]}
    run={async (reason, key) => {
      const { data, error, response } = await apiClient.POST("/api/v1/sourcing/rfqs/{id}/cancel", { params: { path: { id: rfq.id } }, headers: mutationHeaders(key), body: { reason } });
      if (data) { onDone(data); return null; }
      return { message: failure(error as Problem | undefined, "Cancelling the RFQ"), final: response.status === 409 || response.status === 422 };
    }} />;
}
