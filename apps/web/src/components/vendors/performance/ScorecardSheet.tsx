import * as Dialog from "@radix-ui/react-dialog";
import { X } from "@phosphor-icons/react";
import { Fragment, useState } from "react";
import { apiClient } from "../../../api/client";
import Person, { personLabel } from "../../ui/Person";
import { restoreFocus } from "../../../lib/focus";
import { failure, formatDate, formatDateTime } from "../../requisitions/api";
import { useLoad } from "../../requisitions/useLoad";
import { openDispute, type Dispute, type Score } from "./api";
import { AddScoreForm, DisputeForm, ResolveForm } from "./ScoreForms";

type Props = { vendorId: string; categories: string[]; opener: HTMLElement | null; onClose: () => void; onChanged: () => void };
type Panel = { kind: "add" } | { kind: "dispute"; score: Score } | { kind: "resolve"; score: Score; dispute: Dispute };

const monthLabel = (period: string) => new Intl.DateTimeFormat(undefined, { year: "numeric", month: "short" }).format(new Date(`${period}T00:00:00`));

/** §3.6 scorecard sheet (DECISIONS 134). Rows are append-only: an adjustment shows the struck original beside it (VEN-015). */
export default function ScorecardSheet({ vendorId, categories, opener, onClose, onChanged }: Props) {
  const [panel, setPanel] = useState<Panel | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [tail, setTail] = useState<{ rows: Score[]; cursor: string | null } | null>(null);
  const [moreError, setMoreError] = useState<string | null>(null);
  const first = useLoad(async () => {
    const { data, error } = await apiClient.GET("/api/v1/vendors/{id}/scores", { params: { path: { id: vendorId }, query: { limit: 50 } } });
    if (!data) throw new Error(failure(error, "Loading the scorecard"));
    return data;
  }, [vendorId]);
  const rows = [...(first.data?.rows ?? []), ...(tail?.rows ?? [])];
  const cursor = tail ? tail.cursor : first.data?.next_cursor ?? null;
  const loaded = new Set(rows.map(r => r.id));

  const loadMore = async () => {
    setMoreError(null);
    const { data, error } = await apiClient.GET("/api/v1/vendors/{id}/scores", { params: { path: { id: vendorId }, query: { limit: 50, cursor: cursor! } } });
    if (!data) { setMoreError(failure(error, "Loading more scores")); return; }
    setTail({ rows: [...(tail?.rows ?? []), ...data.rows], cursor: data.next_cursor });
  };
  const done = (message: string) => { setPanel(null); setNotice(message); setTail(null); first.reload(); onChanged(); };
  const jump = (id: string) => { const row = document.getElementById(`score-${id}`); row?.scrollIntoView({ block: "center" }); row?.focus(); };
  const link = (id: string, text: string) => loaded.has(id) ? <a href={`#score-${id}`} onClick={e => { e.preventDefault(); jump(id); }}>{text}</a> : <span>{text}</span>;

  return <Dialog.Root open onOpenChange={open => { if (!open) onClose(); }}>
    <Dialog.Portal>
      <Dialog.Overlay className="appr-scrim" />
      <Dialog.Content className="appr-sheet vendor-sheet perf-sheet" aria-describedby="scorecard-desc"
        onCloseAutoFocus={event => { event.preventDefault(); restoreFocus(opener); }}>
        <header className="appr-sheet-head">
          <div><Dialog.Title>Scorecard</Dialog.Title>
            <Dialog.Description id="scorecard-desc" className="visually-hidden">Every score with its source order, scorer and rationale. Nothing is edited in place.</Dialog.Description></div>
          <Dialog.Close className="appr-close" aria-label="Close"><X aria-hidden="true" />Close</Dialog.Close>
        </header>
        <div><button type="button" onClick={() => { setNotice(null); setPanel({ kind: "add" }); }}>Add Score</button></div>
        {notice && <p role="status">{notice}</p>}
        {panel?.kind === "add" && <AddScoreForm vendorId={vendorId} categories={categories} onDone={done} onCancel={() => setPanel(null)} />}
        {first.error ? <div role="alert"><p>{first.error}</p><button type="button" onClick={first.reload}>Try again</button></div>
          : first.loading && !first.data ? <p className="req-skeleton req-skeleton-short" role="status" aria-label="Loading scorecard" />
          : rows.length === 0 ? <p>No scores yet. Add the first one above.</p>
          : <table className="vendor-table perf-table">
            <caption className="visually-hidden">Scores, newest period first</caption>
            <thead><tr><th scope="col">Order</th><th scope="col">Category</th><th scope="col" className="perf-num">Score</th><th scope="col">Rationale</th><th scope="col"><span className="visually-hidden">Actions</span></th></tr></thead>
            <tbody>{rows.map(s => { const open = openDispute(s); return <Fragment key={s.id}>
              <tr id={`score-${s.id}`} tabIndex={-1}>
                <td data-label="Order">{s.source_ref}<span className="req-sub">{monthLabel(s.period)}</span></td>
                <td data-label="Category">{s.category_code}</td>
                <td data-label="Score" className="perf-num">
                  {s.superseded_by ? <>
                    <s className="perf-struck">{s.score}</s><span className="visually-hidden">Original score, replaced by adjustment</span>
                    <span className="req-sub" aria-hidden="true">Replaced</span>{link(s.superseded_by, "See adjustment")}
                  </> : <>{s.score}{s.supersedes && <span className="req-sub">Adjustment {link(s.supersedes, "See original")}</span>}</>}
                </td>
                <td data-label="Rationale">{s.rationale}<span className="req-sub"><Person name={s.scored_by_name} email={s.scored_by_email} />, {formatDateTime(s.scored_at)}</span>
                  {s.disputes.map(d => <p key={d.id} className="perf-dispute">
                    Dispute by <Person name={d.raised_by_name} email={d.raised_by_email} />, {formatDate(d.raised_at)}: {d.reason}
                    <span className="req-sub">{d.resolution ? `${d.resolution === "upheld" ? "Upheld" : "Dismissed"} by ${personLabel(d.resolved_by_name, d.resolved_by_email ?? "")}, ${formatDate(d.resolved_at)}: ${d.resolution_note}` : "Open, waiting for a decision"}</span>
                  </p>)}
                </td>
                <td>{s.superseded_by ? null : open
                  ? <button type="button" aria-label={`Resolve dispute on ${s.source_ref} ${s.category_code}`} onClick={() => { setNotice(null); setPanel({ kind: "resolve", score: s, dispute: open }); }}>Resolve</button>
                  : <button type="button" aria-label={`Dispute ${s.source_ref} ${s.category_code}`} onClick={() => { setNotice(null); setPanel({ kind: "dispute", score: s }); }}>Dispute</button>}</td>
              </tr>
              {panel && panel.kind !== "add" && panel.score.id === s.id && <tr><td colSpan={5}>
                {panel.kind === "dispute"
                  ? <DisputeForm vendorId={vendorId} score={s} onDone={done} onCancel={() => setPanel(null)} />
                  : <ResolveForm vendorId={vendorId} score={s} dispute={panel.dispute} onDone={done} onCancel={() => setPanel(null)} />}
              </td></tr>}
            </Fragment>; })}</tbody>
          </table>}
        {moreError && <p role="alert" className="field-error">{moreError}</p>}
        {cursor && <div><button type="button" onClick={() => void loadMore()}>Load more</button></div>}
      </Dialog.Content>
    </Dialog.Portal>
  </Dialog.Root>;
}
