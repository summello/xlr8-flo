import { ArrowRight, FileText, NotePencil, PencilSimple, Trash } from "@phosphor-icons/react";
import { useState } from "react";
import { apiClient } from "../../api/client";
import StatusPill from "../../components/status/StatusPill";
import ExpiredCodeBadge from "../../components/codes/ExpiredCodeBadge";
import SubstituteCodes from "../../components/codes/SubstituteCodes";
import { CardSkeleton, HistoryCard, LinesCard, LinkedCard, CardState, RequestCard, ReservationCard } from "../../components/requisitions/DetailCards";
import Stepper from "../../components/requisitions/Stepper";
import WithdrawDialog from "../../components/requisitions/WithdrawDialog";
import { failure, formatDateTime, mutationHeaders, type Problem, type Requisition } from "../../components/requisitions/api";
import { useLoad } from "../../components/requisitions/useLoad";
import "../../styles/projects.css";
import "../../styles/requisitions.css";

async function unwrap<T>(result: Promise<{ data?: T; error?: unknown }>, action: string): Promise<T> {
  const { data, error } = await result;
  if (data === undefined) throw new Error(failure(error as Problem | undefined, action));
  return data;
}

export default function RequisitionDetail({ id, navigate, onLoaded }: { id: string; navigate: (href: string) => void; onLoaded?: (title: string) => void }) {
  const main = useLoad(async () => {
    const row = await unwrap(apiClient.GET("/api/v1/requisitions/{id}", { params: { path: { id } } }), "Loading this requisition");
    onLoaded?.(row.title);
    return row;
  }, [id]);
  const lines = useLoad(async () => (await unwrap(apiClient.GET("/api/v1/requisitions/{id}/lines", { params: { path: { id } } }), "Loading the lines")).rows, [id]);
  const links = useLoad(() => unwrap(apiClient.GET("/api/v1/requisitions/{id}/links", { params: { path: { id } } }), "Loading linked records"), [id]);
  const events = useLoad(() => unwrap(apiClient.GET("/api/v1/requisitions/{id}/events", { params: { path: { id } } }), "Loading the history"), [id]);
  const available = useLoad(() => unwrap(apiClient.GET("/api/v1/requisitions/{id}/transitions/available", { params: { path: { id } } }), "Loading the available actions"), [id]);
  const instanceId = main.data?.approval?.instance_id;
  const decisions = useLoad(async () => instanceId ? unwrap(apiClient.GET("/api/v1/approvals/instances/{id}/decisions", { params: { path: { id: instanceId } } }), "Loading the decisions") : [], [instanceId]);
  const instance = useLoad(async () => instanceId ? unwrap(apiClient.GET("/api/v1/approvals/instances/{id}", { params: { path: { id: instanceId } } }), "Loading the approval") : null, [instanceId]);
  const delivery = useLoad(async () => {
    const row = main.data;
    if (!row?.delivery_address_id) return null;
    const { data } = await apiClient.GET("/api/v1/requisitions/lookups/{kind}", { params: { path: { kind: "delivery_address" }, query: { bu_id: row.bu_id, limit: 50 } } });
    return data?.rows.find(o => o.value === row.delivery_address_id)?.label ?? null;
  }, [main.data?.delivery_address_id, main.data?.bu_id]);
  const [toast, setToast] = useState<string | null>(null);
  const [discardError, setDiscardError] = useState<string | null>(null);

  if (main.error) return <section className="req-page" role="alert"><h2>Could not load this requisition</h2><p>{main.error}</p><button type="button" onClick={main.reload}>Try again</button></section>;
  const row = main.data;
  if (!row) return <section className="req-page req-detail" aria-busy="true" aria-label="Requisition, loading">
    <CardSkeleton name="where it is" wide />
    <div className="req-grid-detail"><CardSkeleton name="the request" /><CardSkeleton name="the reservation" /><CardSkeleton name="the history" /><CardSkeleton name="linked records" /><CardSkeleton name="the lines" wide /></div>
  </section>;

  const changed = (next: Requisition, message: string) => {
    main.set(next); lines.reload(); events.reload(); available.reload(); links.reload(); decisions.reload(); setToast(message);
    requestAnimationFrame(() => document.getElementById("where-title")?.focus());
  };
  const can = (to: string) => available.data?.find(a => a.to === to);
  const returned = row.status === "draft" && row.approval?.status === "returned";
  const held = row.reservation != null && row.reservation.remaining !== "0.0000";
  const returnDecision = returned ? [...(decisions.data ?? [])].reverse().find(d => d.outcome === "return") : undefined;
  const approvers = instance.data?.tasks.filter(t => t.status === "open").flatMap(t => t.approver_emails) ?? [];
  const withdrawReady = ["draft", "approval_pending", "sourcing_in_progress"].includes(row.status);
  const withdrawBlocked = withdrawReady && available.data && !can("withdrawn")?.allowed ? can("withdrawn")?.blocked_reasons[0] : undefined;

  const discard = async () => {
    setDiscardError(null);
    const { data, error } = await apiClient.POST("/api/v1/requisitions/{id}/transitions", { params: { path: { id } }, headers: mutationHeaders(crypto.randomUUID(), row.version), body: { to: "withdrawn" } });
    if (data) navigate("/requisitions"); else setDiscardError(failure(error as Problem | undefined, "Discarding the draft"));
  };

  return <section className="req-page req-detail" aria-label={row.standalone ? "Project Request" : "Requisition"}>
    <header className="req-heading">
      <p>{row.number} · {row.project_name ?? "Project Request"} · requested by {row.requester_email}</p>
      <StatusPill docType="requisition" status={row.status} />
      {row.codes_expired && <ExpiredCodeBadge />}
      <div className="req-actions">
        {row.can_substitute_codes && row.codes && <SubstituteCodes target="requisition" id={id} codes={row.codes} onDone={main.reload} />}
        {row.status === "draft" && !returned && <a className="req-link-button" href={`/requisitions/${id}/edit`} onClick={e => { e.preventDefault(); navigate(`/requisitions/${id}/edit`); }}><PencilSimple aria-hidden="true" weight="regular" />Edit draft</a>}
        {row.status === "draft" && !held && can("withdrawn")?.allowed && <button type="button" className="req-destructive" onClick={() => void discard()}><Trash aria-hidden="true" weight="regular" />Discard draft</button>}
        {withdrawReady && (row.status !== "draft" || held) && can("withdrawn")?.allowed && <WithdrawDialog row={row} mode="withdraw" approvers={approvers} onDone={next => changed(next, "Requisition withdrawn.")} onStale={main.reload} />}
        {row.status === "sourcing_in_progress" && (!row.amendment || row.amendment.status === "draft") && <a className="req-link-button" href={`/requisitions/${id}/amend`} onClick={e => { e.preventDefault(); navigate(`/requisitions/${id}/amend`); }}><NotePencil aria-hidden="true" weight="regular" />{row.amendment ? "Continue amendment" : "Amend"}</a>}
        {row.status === "sourcing_in_progress" && row.amendment?.status !== "pending" && (() => {
          const href = `/sourcing/rfqs/new?unit=${encodeURIComponent(row.bu_id)}${row.bu_name ? `&unitName=${encodeURIComponent(row.bu_name)}` : ""}&requisition=${encodeURIComponent(row.number)}`;
          return <a className="req-link-button" href={href} onClick={e => { e.preventDefault(); navigate(href); }}><FileText aria-hidden="true" weight="regular" />Start RFQ</a>;
        })()}
        {row.amendment?.status === "pending" && <p className="req-pill" data-tone="info">Amendment pending approval</p>}
        {can("cancelled")?.allowed && <WithdrawDialog row={row} mode="cancel" approvers={approvers} onDone={next => changed(next, "Requisition cancelled.")} onStale={main.reload} />}
      </div>
      {withdrawBlocked && <p className="field-help">Withdraw is not available: {withdrawBlocked}</p>}
      {discardError && <p role="alert" className="field-error">{discardError}</p>}
    </header>
    {toast && <p className="req-toast" role="status">{toast}</p>}
    {returned && <section className="req-banner" aria-labelledby="returned-title">
      <h2 id="returned-title">Returned for Changes</h2>
      {returnDecision ? <p>{returnDecision.actor_email} returned it on {formatDateTime(returnDecision.decided_at)}{returnDecision.comment ? <>: <q>{returnDecision.comment}</q></> : "."}</p> : <p>An approver returned it for changes.</p>}
      <p>The reservation is held while you amend.</p>
      <div className="req-actions">
        <a className="req-link-button" href={`/requisitions/${id}/edit`} onClick={e => { e.preventDefault(); navigate(`/requisitions/${id}/edit`); }}>Edit and Resubmit</a>
        <a className="form-submit" href={`/requisitions/${id}/resubmit`} onClick={e => { e.preventDefault(); navigate(`/requisitions/${id}/resubmit`); }}>Review and Resubmit <ArrowRight aria-hidden="true" weight="regular" /></a>
      </div>
    </section>}
    <Stepper status={row.status} events={events.data} />
    <div className="req-grid-detail">
      <RequestCard row={row} delivery={delivery.data} />
      <ReservationCard row={row} />
      <section className="req-partial"><CardState name="the history" error={events.error ?? decisions.error} loading={events.loading} hasData={events.data !== null} retry={() => { events.reload(); decisions.reload(); }}><HistoryCard events={events.data ?? []} decisions={decisions.data ?? []} /></CardState></section>
      <section className="req-partial"><CardState name="linked records" error={links.error} loading={links.loading} hasData={links.data !== null} retry={links.reload}><LinkedCard nodes={links.data?.nodes ?? []} partial={links.data?.partial ?? false} /></CardState></section>
      <section className="req-partial req-wide"><CardState name="the lines" error={lines.error} loading={lines.loading} hasData={lines.data !== null} retry={lines.reload}><LinesCard lines={lines.data ?? []} /></CardState></section>
    </div>
  </section>;
}
