import { CheckCircle, PaperPlaneTilt } from "@phosphor-icons/react";
import { useEffect, useRef, useState } from "react";
import { apiClient } from "../../../api/client";
import { CardSkeleton, CardState } from "../DetailCards";
import ReqField from "../ReqField";
import { failure, mutationHeaders, type Problem, type Requisition } from "../api";
import { useLoad } from "../useLoad";
import { FundingEffect, ResultAlert, Versions, WhatChanged, WhatHappens } from "./ChangeCards";
import { baseRevision, classify, latestRevision, type Outcome } from "./model";

async function unwrap<T>(result: Promise<{ data?: T; error?: unknown }>, action: string): Promise<T> {
  const { data, error } = await result;
  if (data === undefined) throw new Error(failure(error as Problem | undefined, action));
  return data;
}

type Done = { headline: string; detail: string };

/** Review changes, then send. `resubmit`: a returned draft against its latest submission. `amend`: the open amendment against the applied version. */
export default function ReviewScreen({ id, kind, navigate, onLoaded }: { id: string; kind: "resubmit" | "amend"; navigate: (href: string) => void; onLoaded?: (title: string) => void }) {
  const amend = kind === "amend";
  const editHref = amend ? `/requisitions/${id}/amend` : `/requisitions/${id}/edit`;
  const row = useLoad(async () => {
    const next = await unwrap(apiClient.GET("/api/v1/requisitions/{id}", { params: { path: { id } } }), "Loading this requisition");
    onLoaded?.(next.title);
    return next;
  }, [id]);
  const changes = useLoad(async () => {
    // An amendment is compared with the applied version it was opened on, so a later applied revision cannot change what is shown.
    let against: number | undefined;
    if (amend) {
      const [open, list] = await Promise.all([
        unwrap(apiClient.GET("/api/v1/requisitions/{id}/amendments/current", { params: { path: { id } } }), "Loading the amendment"),
        unwrap(apiClient.GET("/api/v1/requisitions/{id}/revisions", { params: { path: { id } } }), "Loading the versions"),
      ]);
      against = baseRevision(list, open.revision_no);
    }
    return unwrap(apiClient.GET("/api/v1/requisitions/{id}/changes", { params: { path: { id }, query: against === undefined ? {} : { against } } }), "Loading the changes");
  }, [id, amend]);
  const revisions = useLoad(() => unwrap(apiClient.GET("/api/v1/requisitions/{id}/revisions", { params: { path: { id } } }), "Loading the versions"), [id]);
  const amendment = useLoad(async () => amend ? unwrap(apiClient.GET("/api/v1/requisitions/{id}/amendments/current", { params: { path: { id } } }), "Loading the amendment") : null, [id, amend]);

  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  const [done, setDone] = useState<Done | null>(null);
  const key = useRef<string | null>(null);
  const doneRef = useRef<HTMLElement>(null);
  useEffect(() => { if (done) doneRef.current?.focus(); }, [done]);

  const reloadAll = () => { key.current = null; setOutcome(null); row.reload(); changes.reload(); revisions.reload(); amendment.reload(); };
  const verb = amend ? "Submit Amendment" : "Resubmit";
  const past = amend ? "submitted" : "resubmitted";

  if (row.error) return <section className="req-page" role="alert"><h2>Could not load this requisition</h2><p>{row.error}</p><button type="button" onClick={row.reload}>Try again</button></section>;
  if (!row.data) return <section className="req-page" aria-busy="true" aria-label="Review changes, loading"><div className="req-grid"><div className="req-main"><CardSkeleton name="the changes" /></div><aside className="req-side"><CardSkeleton name="what happens next" /><CardSkeleton name="the versions" /></aside></div></section>;
  const req: Requisition = row.data;
  const link = (href: string, text: string, cls?: string) => <a className={cls} href={href} onClick={e => { e.preventDefault(); navigate(href); }}>{text}</a>;

  const blocked = amend ? (req.amendment?.status === "draft" ? null : "There is no draft amendment to review.") : (req.status === "draft" ? null : "Only a returned draft can be resubmitted.");
  if (blocked && !done) return <section className="req-page"><h2>{req.title}</h2><p>{blocked}</p>{link(amend ? `/requisitions/${id}/amend` : `/requisitions/${id}`, amend ? "Back to the amendment" : "Back to the requisition")}</section>;

  const set = changes.data;
  const next = set ? (set.to === "amendment" && req.amendment ? req.amendment.revision_no : set.from_revision + 1) : 0;

  const submit = async () => {
    if (busy) return;
    setBusy(true); setOutcome(null);
    key.current ??= crypto.randomUUID(); // one key per attempt, reused when the same send is retried
    const text = note.trim() || undefined;
    try {
      const sent = amend
        ? await apiClient.POST("/api/v1/requisitions/{id}/amendments/{revision_no}/submit", { params: { path: { id, revision_no: req.amendment?.revision_no ?? 0 } }, headers: mutationHeaders(key.current, amendment.data?.version), body: { note: text } })
        : await apiClient.POST("/api/v1/requisitions/{id}/transitions", { params: { path: { id } }, headers: mutationHeaders(key.current, req.version), body: { to: "approval_pending", note: text } });
      if (!sent.data) {
        const problem = sent.error as Problem | undefined;
        const found = classify(problem, past);
        setOutcome(found.kind === "other" ? { kind: "other", message: failure(problem, amend ? "Submitting the amendment" : "Resubmitting") } : found);
        return;
      }
      key.current = null;
      const after = sent.data;
      const list = await apiClient.GET("/api/v1/requisitions/{id}/revisions", { params: { path: { id } } }).then(r => r.data ?? null).catch(() => null);
      const n = latestRevision(list, next);
      if (list) revisions.set(list);
      const asked = await whoIsAsked(after);
      setDone(amend && after.amendment?.status !== "pending"
        ? { headline: `Applied. The requisition is now Version ${n}`, detail: "The change took effect without a new approval." }
        : amend ? { headline: "Sent for approval", detail: `The approved version stays in force until it is approved.${asked}` }
          : { headline: `Resubmitted as Version ${n}`, detail: asked.trim() || "The approver has been asked." });
    } catch {
      setOutcome({ kind: "other", message: failure(undefined, amend ? "Submitting the amendment" : "Resubmitting") });
    } finally { setBusy(false); }
  };

  return <section className="req-page" aria-label={amend ? "Review amendment" : "Review changes"}>
    <header className="req-heading">
      <p>{req.number} · {req.title} · amounts in {req.currency}</p>
      {link(`/requisitions/${id}`, "Back to requisition")}
    </header>
    {done && <section ref={doneRef} className="req-toast req-done" role="status" tabIndex={-1} aria-labelledby="done-title">
      <h2 id="done-title">{done.headline}</h2><p>{done.detail}</p>{link(`/requisitions/${id}`, "Back to Requisition", "form-submit")}</section>}
    {outcome && <ResultAlert outcome={outcome} editHref={editHref} go={navigate} reload={reloadAll} />}
    <div className="req-grid">
      <div className="req-main">
        <section className="req-partial">
          {changes.error ? <div role="alert" className="req-card"><h2>Could not load the changes</h2><p>{changes.error}</p><button type="button" onClick={changes.reload}>Try again</button></div>
            : !set ? <CardSkeleton name="the changes" />
              : set.rows.length === 0 ? <section className="req-card"><h2>Nothing has changed yet</h2><p>Make a change first, then come back to review it.</p>{link(editHref, "Back to the editor")}</section>
                : <WhatChanged rows={set.rows} currency={req.currency} was={set.from_revision} now={next} />}
        </section>
        <section className="req-card" aria-labelledby="note-title">
          <header className="req-card-head"><h2 id="note-title">Note to the Approver</h2></header>
          <ReqField id="req-note" label="Note" required={false} help="Optional. The approver sees it with this diff. It is kept if the send fails.">
            {p => <textarea {...p} rows={3} maxLength={1000} value={note} onChange={e => { key.current = null; setNote(e.target.value); }} />}</ReqField>
        </section>
      </div>
      <aside className="req-side">
        {set && <WhatHappens set={set} verb={amend ? "You Submit" : "You Resubmit"} />}
        {set && <FundingEffect set={set} retry={changes.reload} />}
        <section className="req-partial"><CardState name="the versions" error={revisions.error} loading={revisions.loading} hasData={revisions.data !== null} retry={revisions.reload}><Versions revisions={revisions.data ?? []} requester={{ id: req.requester_id, email: req.requester_email }} /></CardState></section>
      </aside>
    </div>
    <div className="req-action-bar">
      <p role="status" className="req-status">{busy ? "Sending" : done ? "Sent" : "Review the changes, then send"}</p>
      {link(editHref, amend ? "Back to the amendment" : "Back to the editor", "req-link-button")}
      <button type="button" className="form-submit" onClick={() => void submit()} aria-busy={busy || undefined}>{done ? <CheckCircle aria-hidden="true" weight="regular" /> : <PaperPlaneTilt aria-hidden="true" weight="regular" />}{busy ? "Sending" : amend ? verb : "Resubmit for Approval"}</button>
    </div>
  </section>;
}

/** " Now with <emails> (<step>)" from the approval instance the submit started; empty when it cannot be read. */
async function whoIsAsked(row: Requisition): Promise<string> {
  if (!row.approval) return "";
  const { data } = await apiClient.GET("/api/v1/approvals/instances/{id}", { params: { path: { id: row.approval.instance_id } } }).catch(() => ({ data: undefined }));
  if (!data) return "";
  const step = data.route.find(n => n.node_id === data.current_node_id) ?? data.route[0];
  const emails = data.tasks.filter(t => t.status === "open").flatMap(t => t.approver_emails);
  const who = emails.length ? emails : step?.approver_emails ?? [];
  return who.length ? ` Now with ${who.join(", ")}${step ? ` at ${step.label}` : ""}.` : "";
}
