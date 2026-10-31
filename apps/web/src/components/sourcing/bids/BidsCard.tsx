import { useState } from "react";
import { apiClient } from "../../../api/client";
import { failure } from "../../requisitions/api";
import { useLoad } from "../../requisitions/useLoad";
import Card from "../Card";
import ChangeContactDialog from "../ChangeContactDialog";
import type { Rfq } from "../api";
import BidSheet from "./BidSheet";
import { bidState, currentBid, type AnyBid } from "./api";

/** The Invitees card with each vendor's bid state and `Record Bid`. Only a caller who can list bids reads them; an `rfq.view` reader gets 403, so the call is never made (E12-S14). */
export default function InviteesCard({ rfq, onChanged }: { rfq: Rfq; onChanged: () => void }) {
  const canList = !!(rfq.can_edit || rfq.can_approve);
  const bids = useLoad<AnyBid[]>(async () => {
    if (!canList) return [];
    const { data, error } = await apiClient.GET("/api/v1/sourcing/rfqs/{id}/bids", { params: { path: { id: rfq.id } } });
    if (!data) throw new Error(failure(error, "Loading bids"));
    return data;
  }, [rfq.id, canList]);
  const [open, setOpen] = useState<string | null>(null);
  const [opener, setOpener] = useState<HTMLElement | null>(null), [now] = useState(() => Date.now());
  const invitees = rfq.invitees ?? [];
  const chosen = invitees.find(i => i.vendor_id === open);
  return <Card title="Invitees" load={bids}>
    {invitees.length === 0 ? <p>No vendors are invited yet.</p> : invitees.map(i => {
      const sent = i.latest_sent_version, deliveries = i.deliveries ?? [], notTold = i.not_told_of ?? [], bounced = deliveries.some(d => d.state === "bounced");
      const states = Object.entries(deliveries.reduce<Record<string, number>>((n, d) => ({ ...n, [d.state]: (n[d.state] ?? 0) + 1 }), {}));
      const bid = currentBid(bids.data ?? [], i.vendor_id);
      return <div className="rfq-invitee" key={i.vendor_id}>
        <strong>{String(i.vendor_snapshot.trading_name ?? "Vendor")}</strong>
        <span className="req-sub">{sent === null || sent === undefined ? "No version sent yet" : `Version ${sent} sent`}</span>
        {states.length > 0 && <span className="req-sub">Email: {states.map(([state, n]) => `${n} ${state}`).join(", ")}</span>}
        {notTold.length > 0 && <span className="req-sub" role="status">Not yet told of version {notTold.join(", ")}</span>}
        {canList && <span className="req-sub">Bid: {bidState(bid, rfq.deadline_at, now)}</span>}
        {bounced && rfq.can_edit && <ChangeContactDialog rfq={rfq} invitee={i} onDone={onChanged} />}
        {rfq.can_edit && ["published", "closed"].includes(rfq.status) && <button type="button" onClick={e => { setOpener(e.currentTarget); setOpen(i.vendor_id); }}>Record Bid</button>}
      </div>;
    })}
    {chosen && <BidSheet key={chosen.vendor_id} rfq={rfq} invitee={chosen} bid={currentBid(bids.data ?? [], chosen.vendor_id)} opener={opener} onClose={() => setOpen(null)} onChanged={() => { bids.reload(); onChanged(); }} />}
  </Card>;
}

/** The Bids card: the count is public to anyone who can see the RFQ; amounts stay sealed until the bidding stop time (SRC-007). */
export function BidsCard({ rfq, navigate }: { rfq: Rfq; navigate: (href: string) => void }) {
  const [now] = useState(() => Date.now());
  const received = rfq.bids_received ?? 0, passed = new Date(rfq.deadline_at).getTime() <= now;
  return <Card title="Bids" load={{ error: null, loading: false, data: rfq, reload: () => undefined }}>
    <p>{received} {received === 1 ? "bid" : "bids"} received.</p>
    {passed ? <p><a href={`/sourcing/rfqs/${rfq.id}/comparison`} onClick={e => { e.preventDefault(); navigate(`/sourcing/rfqs/${rfq.id}/comparison`); }}>Open the bid comparison</a></p>
      : <p className="req-sub">Amounts stay sealed until the bidding stop time.</p>}
  </Card>;
}

