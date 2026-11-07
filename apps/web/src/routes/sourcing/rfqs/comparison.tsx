import { ArrowsClockwise, WarningCircle } from "@phosphor-icons/react";
import { useEffect, useRef, useState } from "react";
import { apiClient } from "../../../api/client";
import ExtendDeadlineDialog from "../../../components/sourcing/ExtendDeadlineDialog";
import BasisCards, { AwardBar } from "../../../components/sourcing/comparison/BasisCards";
import { ByLine, CostBreakdown, Scoring } from "../../../components/sourcing/comparison/Detail";
import Matrix from "../../../components/sourcing/comparison/Matrix";
import Tabs from "../../../components/sourcing/comparison/Tabs";
import VendorSheet from "../../../components/sourcing/comparison/VendorSheet";
import VerdictStrip from "../../../components/sourcing/comparison/VerdictStrip";
import { isSealed, type AnyComparison, type BasisName, type Comparison, type Vendor } from "../../../components/sourcing/comparison/api";
import { failure, formatDateTime, mutationHeaders, type Problem } from "../../../components/requisitions/api";
import { useLoad } from "../../../components/requisitions/useLoad";
import type { Rfq } from "../../../components/sourcing/api";
import "../../../styles/approvals.css";
import "../../../styles/projects.css";
import "../../../styles/requisitions.css";
import "../../../styles/sourcing.css";
import "../../../styles/comparison.css";

const TABS = [["overview", "Overview"], ["by-line", "By Line"], ["cost", "Cost Breakdown"], ["scoring", "Scoring"]] as const;
const NARROW = 1000; // content width, not viewport (pages/rfq-comparison.md 5); MASTER has no token for a container cutoff

/** pages/rfq-comparison.md: who should win, and a hand-off to the award. The page computes no figure; every number, marker and verdict is E12-S06's response. */
export default function RfqComparison({ id, navigate }: { id: string; navigate: (href: string) => void }) {
  const rfq = useLoad(async (): Promise<Rfq> => {
    const { data, error, response } = await apiClient.GET("/api/v1/sourcing/rfqs/{id}", { params: { path: { id } } });
    if (!data) throw new Error(response?.status === 404 ? "This RFQ does not exist or you cannot see it." : failure(error, "Loading the RFQ"));
    return data;
  }, [id]);
  const comparison = useLoad(async (): Promise<AnyComparison> => {
    const { data, error } = await apiClient.GET("/api/v1/sourcing/rfqs/{id}/comparison", { params: { path: { id } } });
    if (!data) throw new Error(failure(error, "Loading the comparison"));
    return data;
  }, [id]);
  const [tab, setTab] = useState<(typeof TABS)[number][0]>("overview"), [basis, setBasis] = useState<BasisName>("lowest_cost_supplier");
  const [sheet, setSheet] = useState<{ vendor: string; opener: HTMLElement } | null>(null);
  const [width, setWidth] = useState(Infinity), root = useRef<HTMLElement>(null);
  const [refreshing, setRefreshing] = useState<string | null>(null), key = useRef<string | null>(null);
  useEffect(() => { if (rfq.data) document.title = `Bid Comparison · ${rfq.data.number}`; }, [rfq.data]);
  useEffect(() => {
    const node = root.current;
    if (!node || typeof ResizeObserver === "undefined") return;
    const watch = new ResizeObserver(([entry]) => setWidth(entry!.contentRect.width));
    watch.observe(node);
    return () => watch.disconnect();
  }, [rfq.data, comparison.data]);

  const refresh = async () => {
    key.current ??= crypto.randomUUID();
    setRefreshing("working");
    try {
      const { data, error } = await apiClient.POST("/api/v1/sourcing/rfqs/{id}/comparison/refresh-rate", { params: { path: { id } }, headers: mutationHeaders(key.current) });
      if (data) { key.current = null; comparison.set(data); setRefreshing(null); } else setRefreshing(failure(error as Problem | undefined, "Refreshing the rate"));
    } catch { setRefreshing(failure(undefined, "Refreshing the rate")); }
  };

  const back = <a href={`/sourcing/rfqs/${id}`} onClick={e => { e.preventDefault(); navigate(`/sourcing/rfqs/${id}`); }}>Back to the RFQ</a>;
  const failed = rfq.error ?? comparison.error;
  if (failed) return <section className="req-page cmp" aria-label="Bid comparison"><div role="alert"><p>{comparison.error ? "Could not load the comparison. Nothing was opened." : failed}</p><p className="req-sub">{failed}</p><button type="button" onClick={() => { rfq.reload(); comparison.reload(); }}>Try again</button></div>{back}</section>;
  if (!rfq.data || !comparison.data) return <section className="req-page cmp" aria-label="Bid comparison" aria-busy="true"><p className="req-skeleton" role="status" aria-label="Loading comparison" /></section>;
  const r = rfq.data, c = comparison.data;
  const head = <header className="cmp-head"><h2>{r.number} · {r.title}</h2>{back}</header>;

  if (isSealed(c)) return <section className="req-page cmp" aria-label="Bid comparison">{head}
    <div className="req-card" role="status"><h3>{r.bids_received} Received, sealed</h3>
      <p>Prices, terms and scores stay hidden for everyone until the bidding stop time, {formatDateTime(r.deadline_at)}. The comparison opens by itself then.</p></div></section>;
  if (c.vendors.length === 0) return <section className="req-page cmp" aria-label="Bid comparison">{head}
    <div className="req-card"><h3>No Bids Received</h3><p>Extend the deadline or invite more vendors.</p>
      <div className="req-actions">{r.can_edit && <ExtendDeadlineDialog rfq={r} onDone={next => { rfq.set(next); comparison.reload(); }} />}
        <a className="req-link-button" href={`/sourcing/rfqs/${id}`} onClick={e => { e.preventDefault(); navigate(`/sourcing/rfqs/${id}`); }}>Edit Invitations</a></div></div></section>;

  const open = (v: Vendor, opener: HTMLElement) => setSheet({ vendor: v.vendor_id, opener });
  const sheetVendor = sheet && c.vendors.find(v => v.vendor_id === sheet.vendor);
  const narrow = width < NARROW, tiny = width < 560;
  return <section className="req-page cmp" aria-label="Bid comparison" ref={root} data-narrow={narrow || undefined} data-tiny={tiny || undefined}>
    {head}
    {c.stale_rate && <div className="cmp-stale req-pill" data-tone="warning" role="status"><WarningCircle aria-hidden="true" weight="regular" />Exchange rates are older than 7 days (rate date {c.rate_date}).
      {r.can_edit && <button type="button" onClick={() => void refresh()} disabled={refreshing === "working"}><ArrowsClockwise aria-hidden="true" weight="regular" />Refresh Rate</button>}</div>}
    {refreshing && refreshing !== "working" && <div role="alert"><p>{refreshing}</p></div>}
    <Tabs label="Comparison views" idPrefix="cmp" tabs={TABS} value={tab} onChange={setTab}>
      {tab === "overview" && <>
        <BasisCards c={c} basis={basis} onChange={setBasis} />
        <VerdictStrip c={c} open={open} />
        <Matrix c={c} narrow={narrow} open={open} />
      </>}
      {tab === "by-line" && <ByLine c={c} basis={basis} />}
      {tab === "cost" && <CostBreakdown c={c} />}
      {tab === "scoring" && <Scoring c={c} id={id} />}
    </Tabs>
    <AwardBar c={c as Comparison} basis={basis} onStart={() => navigate(`/sourcing/rfqs/${id}/award?basis=${basis}`)} />
    {sheetVendor && sheet && <VendorSheet c={c} rfq={r} v={sheetVendor} opener={sheet.opener} onClose={() => setSheet(null)} />}
    {/* ponytail: a vendor minimum order value is not enforced in v1 (no field, the engine computes nothing); upgrade path is an award-time check in E12-S08. */}
  </section>;
}
