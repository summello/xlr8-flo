import * as Dialog from "@radix-ui/react-dialog";
import { ArrowRight, Info, WarningCircle, X } from "@phosphor-icons/react";
import { useCallback, useEffect, useRef, useState } from "react";

import { apiClient } from "../../api/client";
import type { components } from "../../api/generated/schema";
import { writeHeaders } from "../../features/auth/api";
import ExpiredCodeBadge from "../codes/ExpiredCodeBadge";
import Money from "../ui/Money";
import AwardEvidence from "./evidence/AwardEvidence";
import PurchaseOrderEvidence from "./evidence/PurchaseOrderEvidence";
import VendorEvidence from "./evidence/VendorEvidence";
import InboxStatus from "./InboxStatus";
import type { InboxRow, Tab } from "./inbox-model";
import { stepLabel } from "./inbox-model";
import Person, { personLabel } from "../ui/Person";

type Decision = components["schemas"]["DecisionRead"];
type Instance = components["schemas"]["InstanceRead"];
type Explanation = components["schemas"]["Explanation"];
type Result = components["schemas"]["DecisionResult"];
type Outcome = components["schemas"]["DecisionWrite"]["outcome"];
type Refusal = { kind: "reason" | "stale" | "closed" | "codes" | "other"; text: string; version?: number };

const DONE: Record<Exclude<Outcome, "comment">, string> = {
  approve: "Approved",
  return: "Returned for Changes",
  reject: "Rejected",
};

type Props = {
  row: InboxRow;
  tab: Tab;
  next: InboxRow | undefined;
  onOpen: (row: InboxRow) => void;
  onClose: () => void;
  onDecided: () => void;
  refreshRow: (row: InboxRow) => Promise<InboxRow | undefined>;
};

export default function DecisionPanel({ row, tab, next, onOpen, onClose, onDecided, refreshRow }: Props) {
  const [version, setVersion] = useState(row.document_version);
  const [instance, setInstance] = useState<Instance>();
  const [trail, setTrail] = useState<Decision[]>();
  const [trailFailed, setTrailFailed] = useState(false);
  const [why, setWhy] = useState<Explanation>();
  const [whyFailed, setWhyFailed] = useState(false);
  const [foldOpen, setFoldOpen] = useState(false);
  const [comment, setComment] = useState("");
  const [mode, setMode] = useState<"" | "return_to_draft" | "permanent">("");
  const [modeError, setModeError] = useState("");
  const [project, setProject] = useState("");
  const [projects, setProjects] = useState<components["schemas"]["ProjectRead"][]>([]);
  const [subDepartment, setSubDepartment] = useState("");
  const [subLedger, setSubLedger] = useState("");
  const [codesRefused, setCodesRefused] = useState(false);
  // Known up front from the inbox row; a refusal reveals both fields when the row carried no state.
  const unusable = (state?: { status: string }) => state !== undefined && state.status !== "usable" && state.status !== "missing";
  const needDepartment = row.codes ? unusable(row.codes.department) : codesRefused;
  const needLedger = row.codes ? unusable(row.codes.ledger_account) : codesRefused;
  const codesOpen = needDepartment || needLedger;
  const [refusal, setRefusal] = useState<Refusal>();
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<Result>();
  const [closedByServer, setClosedByServer] = useState(false);
  const close = useRef<HTMLButtonElement>(null);
  const alert = useRef<HTMLDivElement>(null);
  const back = useRef<HTMLButtonElement>(null);
  const key = useRef(crypto.randomUUID());
  const rotateKey = () => { key.current = crypto.randomUUID(); };

  const loadTrail = useCallback(async () => {
    const response = await apiClient.GET("/api/v1/approvals/instances/{id}/decisions", { params: { path: { id: row.instance_id } } });
    setTrail(response.data);
    setTrailFailed(!response.data);
  }, [row.instance_id]);
  const loadWhy = useCallback(async () => {
    setWhyFailed(false);
    try {
      const response = await apiClient.GET("/api/v1/approvals/instances/{id}/explanation", { params: { path: { id: row.instance_id } } });
      setWhy(response.data);
      setWhyFailed(!response.data);
      if (!response.data) setFoldOpen(true);
    } catch { setWhyFailed(true); setFoldOpen(true); }
  }, [row.instance_id]);

  useEffect(() => {
    let active = true;
    void apiClient.GET("/api/v1/approvals/instances/{id}", { params: { path: { id: row.instance_id } } }).then((r) => { if (active) setInstance(r.data); });
    void Promise.resolve().then(() => { if (active) { void loadTrail(); void loadWhy(); } });
    return () => { active = false; };
  }, [row.instance_id, loadTrail, loadWhy]);

  const task = instance?.tasks.find((t) => t.id === row.task_id);
  const closedNow = closedByServer || (instance !== undefined && (instance.status !== "running" || task?.status !== "open"));
  const covering = row.status_word.startsWith("Covering ");
  const passed = row.status_word.startsWith("With ");
  const canDecide = tab !== "completed" && !closedNow;
  const finalStep = row.step.index === row.step.of;
  const canAssign = canDecide && row.document_type === "requisition" && finalStep;

  useEffect(() => {
    if (!canAssign) return;
    let active = true;
    void apiClient.GET("/api/v1/projects", { params: { query: { status: "active", page_size: 50 } } }).then((r) => { if (active && r.data) setProjects(r.data.rows); });
    return () => { active = false; };
  }, [canAssign]);

  useEffect(() => { if (refusal) alert.current?.focus(); }, [refusal]);
  useEffect(() => { if (result) back.current?.focus(); }, [result]);

  async function submit(outcome: Outcome) {
    setNotice("");
    setModeError("");
    const text = comment.trim();
    if (outcome !== "approve" && text === "") {
      setRefusal({ kind: "reason", text: outcome === "comment" ? "Not posted. Write a comment first." : "Not decided. A reason is needed." });
      return;
    }
    if (outcome === "reject" && mode === "") {
      setModeError("Choose whether to return the request or reject it permanently.");
      setRefusal(undefined);
      document.getElementById("appr-mode-return")?.focus();
      return;
    }
    const assigned = projects.find((p) => `${p.number} ${p.name}` === project.trim());
    setRefusal(undefined);
    setBusy(true);
    try {
      const response = await apiClient.POST("/api/v1/approvals/tasks/{task_id}/decisions", {
        params: { path: { task_id: row.task_id } },
        headers: { ...writeHeaders(), "Idempotency-Key": key.current },
        body: {
          outcome,
          document_version: version,
          ...(text ? { comment: text } : {}),
          ...(outcome === "reject" ? { reject_mode: mode || undefined } : {}),
          ...(outcome === "approve" && assigned ? { assign_project_id: assigned.id } : {}),
          ...(outcome === "approve" && (subDepartment.trim() || subLedger.trim()) ? { substitutions: {
            ...(subDepartment.trim() ? { department_code: subDepartment.trim() } : {}),
            ...(subLedger.trim() ? { ledger_account_code: subLedger.trim() } : {}),
          } } : {}),
        },
      });
      if (response.data) {
        rotateKey();
        if (outcome === "comment") {
          setComment("");
          setNotice("Comment posted. Nothing was decided.");
          await loadTrail();
        } else {
          setResult(response.data);
          onDecided();
        }
        return;
      }
      const problem = response.error;
      const kind = problem?.checks?.problem;
      if (kind === "stale_version") {
        const fresh = await refreshRow(row);
        setRefusal({ kind: "stale", text: "Not decided. This requisition changed.", version: fresh?.document_version });
      }
      else if (kind === "code_expired") { setCodesRefused(true); setRefusal({ kind: "codes", text: `Not approved. ${problem?.detail ?? "A master code has expired."} Enter a replacement below, or reject. Your comment is kept.` }); }
      else if (kind === "code_usable" || kind === "code_unusable") setRefusal({ kind: "codes", text: `Not approved. ${problem?.errors?.[0]?.message ?? "Check the replacement code."} Your comment is kept.` });
      else if (kind === "task_closed" || kind === "already_decided") { setClosedByServer(true); setRefusal({ kind: "closed", text: "Already Decided. Someone else decided this first; nothing was recorded from you." }); }
      else if (response.response.status === 422 && problem?.errors?.some((e) => e.field === "comment")) setRefusal({ kind: "reason", text: "Not decided. A reason is needed." });
      else setRefusal({ kind: "other", text: `Not decided. ${problem?.detail ?? "Something went wrong."} Your comment is kept. Try again.` });
    } catch {
      setRefusal({ kind: "other", text: "Not decided. The server could not be reached. Your comment is kept. Try again." });
    } finally {
      setBusy(false);
    }
  }

  function loadVersion(next: number) {
    setVersion(next);
    rotateKey();
    setRefusal(undefined);
    setNotice(`Loaded version ${next}. Review it, then decide. Your comment is kept.`);
    void loadTrail();
  }

  return (
    <Dialog.Root open onOpenChange={(open) => { if (!open) onClose(); }}>
      <Dialog.Portal>
        <Dialog.Overlay className="appr-scrim" />
        <Dialog.Content
          className="appr-sheet"
          aria-modal="true"
          aria-describedby="appr-sheet-desc"
          onOpenAutoFocus={(event) => { event.preventDefault(); close.current?.focus(); }}
          onCloseAutoFocus={(event) => event.preventDefault()}
        >
          <header className="appr-sheet-head">
            <div>
              <p className="appr-meta"><span className="appr-number">{row.number}</span> · {row.document_type} · v{version}</p>
              <Dialog.Title>{row.title}</Dialog.Title>
              <Dialog.Description id="appr-sheet-desc" className="visually-hidden">Review the request and record your decision. Nothing is decided by opening this panel.</Dialog.Description>
            </div>
            <InboxStatus word={result ? DONE[result.decision.outcome as keyof typeof DONE] ?? row.status_word : row.status_word} />
            <Dialog.Close ref={close} className="appr-close" aria-label="Close"><X aria-hidden="true" />Close</Dialog.Close>
          </header>

          {result ? (
            <div className="appr-result" role="status">
              <svg className="appr-tick" viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12.5l4.5 4.5L19 7.5" /></svg>
              <h3>{DONE[result.decision.outcome as keyof typeof DONE] ?? "Recorded"}</h3>
              <p>{result.instance.status === "running" ? "The request moves to the next approver." : `The request is now ${result.instance.status}.`}</p>
              <p className="appr-meta">Recorded: you, <time dateTime={result.decision.decided_at}>{formatDate(result.decision.decided_at)}</time>, version {result.decision.document_version}. Decisions cannot be edited.</p>
              <footer className="appr-actions">
                <button ref={back} type="button" onClick={onClose}>Back to Inbox</button>
                {next && <button type="button" className="appr-primary" onClick={() => onOpen(next)}>Next: {next.number}<ArrowRight aria-hidden="true" /></button>}
              </footer>
            </div>
          ) : (
            <div className="appr-sheet-body">
              <Notices tab={tab} row={row} covering={covering} passed={passed} closed={closedNow} />
              {notice && <p role="status" className="appr-note"><Info aria-hidden="true" />{notice}</p>}
              {row.codes_expired && <ExpiredCodeBadge />}
              <dl className="appr-facts">
                <div><dt>Requested by</dt><dd><Person name={row.requester_name} email={row.requester_email} /></dd></div>
                <div><dt>Amount</dt><dd className="appr-amount">{row.amount ? <Money value={row.amount.amount} currency={row.amount.currency} /> : "Not applicable"}</dd></div>
                <div><dt>Version</dt><dd>v{version}</dd></div>
                <div><dt>Step</dt><dd>{stepLabel(row)}</dd></div>
                {row.requester_note && <div><dt>Note from the requester</dt><dd className="appr-requester-note">{row.requester_note}</dd></div>}
              </dl>
              {!(row.document_type === "award" && instance?.evidence) && <a className="appr-link" href={row.href}>Open the full {row.document_type === "vendor" ? "vendor" : row.document_type === "award" ? "award" : row.document_type === "po" || row.document_type === "po_change" ? "purchase order" : "requisition"}<ArrowRight aria-hidden="true" /></a>}
              {row.document_type === "vendor" && instance?.evidence && <VendorEvidence evidence={instance.evidence} />}
              {row.document_type === "award" && instance?.evidence && <AwardEvidence evidence={instance.evidence} />}
              {row.document_type === "po" && instance?.evidence && <PurchaseOrderEvidence evidence={instance.evidence} />}

              <section aria-labelledby="appr-trail">
                <h3 id="appr-trail">Decision Trail <span className="appr-count">{(trail?.length ?? 0) + (canDecide ? 1 : 0)}</span></h3>
                {trailFailed && <p role="alert">The trail could not load. <button type="button" onClick={() => void loadTrail()}>Try again</button></p>}
                <ol className="appr-trail">
                  {canDecide && <li data-waiting="true"><strong>Waiting for you</strong>, step {stepLabel(row)}{row.due_at && <>, due <time dateTime={row.due_at}>{formatDate(row.due_at)}</time></>}</li>}
                  {[...(trail ?? [])].reverse().map((d) => (
                    <li key={d.id}>
                      <strong>{d.step_label ?? "Request"}: {d.outcome}</strong> by <Person name={d.actor_name} email={d.actor_email} />{d.on_behalf_of_email && <> on behalf of <Person name={d.on_behalf_of_name} email={d.on_behalf_of_email} /></>}, <time dateTime={d.decided_at}>{formatDate(d.decided_at)}</time>
                      {d.comment && <p className="appr-trail-note">{d.comment}</p>}
                    </li>
                  ))}
                </ol>
              </section>

              {refusal && (
                <div id="sheet-err" role="alert" tabIndex={-1} ref={alert} className="appr-refusal">
                  <WarningCircle aria-hidden="true" /> <span>{refusal.text}</span>
                  {refusal.kind === "stale" && refusal.version !== undefined && <button type="button" onClick={() => loadVersion(refusal.version!)}>Load Version {refusal.version}</button>}
                </div>
              )}

              {canDecide && (
                <form className="appr-decide" noValidate onSubmit={(event) => event.preventDefault()}>
                  <label htmlFor="appr-comment">Comment or Reason <span className="appr-tag" data-done={comment.trim() !== "" || undefined}>{comment.trim() !== "" ? "Done" : "Needed to reject or return"}</span></label>
                  <textarea id="appr-comment" rows={4} value={comment} aria-invalid={refusal?.kind === "reason" || undefined} aria-describedby="appr-comment-hint" onChange={(event) => { setComment(event.target.value); rotateKey(); }} />
                  <p id="appr-comment-hint" className="appr-meta">Recorded with your name{covering ? `, the delegation you act under (${personLabel(row.on_behalf_of_name, row.on_behalf_of_email ?? "")})` : ""}, and the version you decided ({version}).</p>
                  <fieldset aria-describedby={modeError ? "appr-mode-error" : undefined} aria-invalid={modeError ? true : undefined}>
                    <legend>If you reject <span className="appr-tag">Required to reject</span></legend>
                    <label><input id="appr-mode-return" type="radio" name="appr-mode" checked={mode === "return_to_draft"} onChange={() => { setMode("return_to_draft"); setModeError(""); rotateKey(); }} /> Return to the requester to fix</label>
                    <label><input type="radio" name="appr-mode" checked={mode === "permanent"} onChange={() => { setMode("permanent"); setModeError(""); rotateKey(); }} /> Reject permanently</label>
                    {modeError && <p id="appr-mode-error" className="field-error"><WarningCircle aria-hidden="true" weight="regular" /><span>{modeError}</span></p>}
                  </fieldset>
                  {codesOpen ? (
                    <fieldset className="appr-assign">
                      <legend>Expired Code <span className="appr-tag">Replace to approve</span></legend>
                      {needDepartment && <>
                        <label htmlFor="appr-sub-department">Replacement department code{row.codes ? ` for ${row.codes.department.code}` : ""}</label>
                        <input id="appr-sub-department" autoComplete="off" value={subDepartment} onChange={(event) => { setSubDepartment(event.target.value); rotateKey(); }} />
                      </>}
                      {needLedger && <>
                        <label htmlFor="appr-sub-ledger">Replacement ledger account code{row.codes ? ` for ${row.codes.ledger_account.code}` : ""}</label>
                        <input id="appr-sub-ledger" autoComplete="off" value={subLedger} onChange={(event) => { setSubLedger(event.target.value); rotateKey(); }} />
                      </>}
                      <p className="appr-meta">The reservation stays on the original code. The substitution is audited. You can also reject without a substitute.</p>
                    </fieldset>
                  ) : null}
                  {canAssign && (
                    <div className="appr-assign">
                      <label htmlFor="appr-project">Assign to an existing project <span className="appr-meta">(optional)</span></label>
                      <input id="appr-project" list="appr-projects" autoComplete="off" value={project} onChange={(event) => { setProject(event.target.value); rotateKey(); }} aria-describedby="appr-project-hint" />
                      <datalist id="appr-projects">{projects.map((p) => <option key={p.id} value={`${p.number} ${p.name}`} label={p.codes_expired ? "Expired code" : undefined} />)}</datalist>
                      <p id="appr-project-hint" className="appr-meta">{project.trim() === "" ? "Leave empty and a new project is created and funded with the approved amount." : "The approved amount is drawn from this project."}</p>
                    </div>
                  )}
                </form>
              )}

              <details className="appr-fold" open={foldOpen} onToggle={(event) => setFoldOpen(event.currentTarget.open)}>
                <summary>Why It Reached You <span className="appr-meta">for audit</span></summary>
                {whyFailed ? <p role="alert">This explanation could not load. Decisions are still possible. <button type="button" onClick={() => void loadWhy()}>Try again</button></p> : why ? (
                  <div>
                    <p>{why.why_this_workflow}</p>
                    <ol>
                      {why.steps.map((step) => "members" in step ? (
                        <li key={step.node_id}><strong>{step.name}</strong>: {step.rule}. Members: {step.members.map((m) => personLabel(m.name, m.email)).join(", ") || "none"}.{step.excluded.map((e) => <span key={e.user_id} className="appr-meta"> <Person name={e.name} email={e.email} /> excluded: {e.detail}.</span>)}</li>
                      ) : (
                        <li key={step.node_id}><strong>{step.name}</strong>: took {step.taken}.</li>
                      ))}
                    </ol>
                    <p className="appr-meta">Members are checked when a decision is made.</p>
                  </div>
                ) : <p role="status">Loading the explanation</p>}
              </details>

              <footer className="appr-actions">
                <Dialog.Close className="appr-secondary" type="button">Close</Dialog.Close>
                {canDecide && (
                  <>
                    <button type="button" aria-busy={busy} onClick={() => void submit("comment")}>Post Comment</button>
                    <button type="button" aria-busy={busy} onClick={() => void submit("return")}>Return for Changes</button>
                    <button type="button" aria-busy={busy} onClick={() => void submit("reject")}>Reject</button>
                    <button type="button" aria-busy={busy} className="appr-primary" onClick={() => void submit("approve")}>Approve</button>
                  </>
                )}
              </footer>
            </div>
          )}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

function Notices({ tab, row, covering, passed, closed }: { tab: Tab; row: InboxRow; covering: boolean; passed: boolean; closed: boolean }) {
  const items: string[] = [];
  if (tab === "completed") items.push("You Decided This");
  else if (closed) items.push("Already Decided");
  if (covering) items.push(`You Are Covering For ${row.on_behalf_of_email ? personLabel(row.on_behalf_of_name, row.on_behalf_of_email) : "someone"}`);
  if (passed) items.push(row.status_word);
  if (items.length === 0) return null;
  return <div className="appr-notices">{items.map((item) => <p key={item} className="appr-notice"><Info aria-hidden="true" />{item}</p>)}</div>;
}

function formatDate(value: string): string {
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(value));
}
