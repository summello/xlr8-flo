import { ArrowUUpLeft } from "@phosphor-icons/react";
import { apiClient } from "../../api/client";
import { failure, mutationHeaders, type Problem } from "../requisitions/api";
import { useRef } from "react";
import Money from "../ui/Money";
import ReasonDialog from "./ReasonDialog";
import type { Award, Rfq } from "./api";

const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;

/** SRC-017, decision 213: the award's quantities reopen and only what its approval returned to the main project moves back. */
export default function SupersedeAwardDialog({ rfq, award, onDone }: { rfq: Rfq; award: Award; onDone: (next: Rfq) => void }) {
  const superseded = useRef(false); // the server has settled it; a retry only refreshes the RFQ
  const vendors = new Set(award.lines.map(l => l.vendor_id)).size;
  const invitees = rfq.invitees?.length ?? 0, bids = rfq.bids_received ?? 0;
  return <ReasonDialog trigger={{ label: "Supersede Award", icon: ArrowUUpLeft }} title="Supersede Award" keep="Keep Award" confirm="Supersede Award" busy="Superseding…"
    intro={<>Award {award.number} (<Money value={award.total} currency={award.currency} zero="value" />) will be replaced. It stays readable in the history, and {rfq.number} returns to evaluation.</>}
    rows={[
      <><strong>Award.</strong> {plural(award.lines.length, "awarded line")} to {plural(vendors, "vendor")} {award.lines.length === 1 ? "is" : "are"} marked superseded and kept on record.</>,
      <><strong>Invitees.</strong> {plural(invitees, "invitee")} {invitees === 1 ? "is" : "are"} not told anything by this action.</>,
      <><strong>Bids.</strong> {plural(bids, "submitted bid")} stay{bids === 1 ? "s" : ""} on record and can be awarded again.</>,
      <><strong>Requisition quantities.</strong> The awarded quantities reopen for sourcing.</>,
      <><strong>Budget.</strong> Only the amount this award&rsquo;s approval returned to the main project moves back, nothing if the award used the whole scope. It is refused if the main project no longer has that amount.</>,
      <><strong>Purchase orders.</strong> Refused if a purchase order was created from this award.</>,
    ]}
    run={async (reason, key) => {
      if (!superseded.current) {
        const { data, error, response } = await apiClient.POST("/api/v1/sourcing/rfqs/{id}/award/supersede", { params: { path: { id: rfq.id } }, headers: mutationHeaders(key), body: { reason } });
        if (!data) return { message: failure(error as Problem | undefined, "Superseding the award"), final: response.status === 409 || response.status === 422 };
        superseded.current = true;
      }
      const fresh = await apiClient.GET("/api/v1/sourcing/rfqs/{id}", { params: { path: { id: rfq.id } } });
      if (!fresh.data) return { message: "The award was superseded, but the RFQ could not be reloaded. Press Supersede Award again to reload it; nothing is sent twice.", final: false };
      onDone(fresh.data);
      return null;
    }} />;
}
