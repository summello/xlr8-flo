import { useEffect, useRef, useState } from "react";
import { apiClient } from "../../api/client";
import Person, { personLabel } from "../../components/ui/Person";
import Signal from "../../components/ui/Signal";
import { sentSignal, type Po, type PoTransmission } from "../../components/purchasing/api";
import IssueStage from "../../components/purchasing/issue/IssueStage";
import ChangeRecipient from "../../components/purchasing/transmissions/ChangeRecipient";
import { currentSend, FAILED, recovery, TERMINAL } from "../../components/purchasing/transmissions/recovery";
import { useResend } from "../../components/purchasing/transmissions/useResend";
import { failure, type Problem } from "../../components/requisitions/api";
import { useLoad } from "../../components/requisitions/useLoad";
import "../../styles/projects.css";
import "../../styles/requisitions.css";
import "../../styles/purchase-orders.css";

// ponytail: polls the transmissions list; stops after 2 minutes ("Still sending"). A push channel would replace both.
const POLL_MS = 5000, POLL_CAP_MS = 120000;
const reduced = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

export default function PurchaseOrderIssued({ id, navigate }: { id: string; navigate: (href: string) => void }) {
  const po = useLoad(async () => {
    const { data, error, response } = await apiClient.GET("/api/v1/purchase-orders/{id}", { params: { path: { id } } });
    if (!data) throw new Error(response?.status === 404 ? "This purchase order does not exist or you cannot see it." : failure(error as Problem | undefined, "Loading the purchase order"));
    return data;
  }, [id]);
  const rows = useLoad(async () => {
    const { data, error } = await apiClient.GET("/api/v1/purchase-orders/{id}/transmissions", { params: { path: { id } } });
    if (!data) throw new Error(failure(error as Problem | undefined, "Loading transmissions"));
    return data;
  }, [id]);
  const back = <a href={`/purchase-orders/${id}`} onClick={e => { e.preventDefault(); navigate(`/purchase-orders/${id}`); }}>Back to the purchase order</a>;
  const error = po.error ?? rows.error;
  if (error && !(po.data && rows.data)) return <section className="req-page" aria-label="Purchase order issued"><div role="alert"><p>{error}</p><button type="button" onClick={() => { po.reload(); rows.reload(); }}>Try again</button></div>{back}</section>;
  if (!po.data || !rows.data) return <section className="req-page" aria-label="Purchase order issued"><div className="req-card req-card-skeleton" aria-busy="true"><p className="req-skeleton" role="status" aria-label="Loading the issued order" /></div></section>;
  if (po.data.status !== "issued") return <section className="req-page" aria-label="Purchase order issued"><p>This purchase order has not been issued.</p>{back}</section>;
  return <Moment po={po.data} id={id} rows={rows.data} setRows={rows.set} back={back} />;
}

function Moment({ po, id, rows, setRows, back }: { po: Po; id: string; rows: PoTransmission[]; setRows: (r: PoTransmission[]) => void; back: React.ReactNode }) {
  const storage = `po-issue-played:${id}`;
  const [motion] = useState(() => ({ reduced: reduced(), play: !reduced() && !sessionStorage.getItem(storage) }));
  const [step, setStep] = useState(motion.play ? 0 : 3);
  useEffect(() => { if (motion.play) sessionStorage.setItem(storage, "1"); }, [storage, motion]);

  const current = currentSend(rows, id);
  const failed = current.filter(r => FAILED.includes(r.result));
  const canChange = po.vendor_status !== null;
  const plan = recovery(current, canChange);
  const vendor = typeof po.vendor_snapshot.trading_name === "string" ? po.vendor_snapshot.trading_name : "the vendor";
  const attempt = current[0]?.attempt ?? 0;

  // Polling: while any row of the current send is queued or sent, until the cap.
  const [capped, setCapped] = useState(false), [tick, setTick] = useState(0);
  const since = useRef(0);
  const pending = current.some(r => !TERMINAL.includes(r.result));
  useEffect(() => {
    if (!pending || capped) return;
    since.current ||= Date.now();
    const t = setTimeout(async () => {
      if (Date.now() - since.current >= POLL_CAP_MS) { setCapped(true); return; }
      try {
        const { data } = await apiClient.GET("/api/v1/purchase-orders/{id}/transmissions", { params: { path: { id } } });
        if (data) setRows(data);
      } catch { /* the next tick tries again */ }
      setTick(n => n + 1);
    }, POLL_MS);
    return () => clearTimeout(t);
  }, [pending, capped, tick, id, setRows]);

  // Resend with the same recipients.
  const resendRef = useRef<HTMLButtonElement>(null), changeRef = useRef<HTMLButtonElement>(null), message = useRef<HTMLParagraphElement>(null);
  const received = (data: PoTransmission[]) => { setRows(data); since.current = 0; setCapped(false); setTick(n => n + 1); };
  const { resend, busy, refusal, clearRefusal } = useResend(id, received);
  const sent = (data: PoTransmission[]) => { received(data); clearRefusal(); };

  // The recovery is focused when a failure appears, and again when a new attempt fails.
  const focus = plan?.focus;
  useEffect(() => {
    if (step < 3 || !focus) return;
    // The shell moves focus to <main> in a frame after a route change; wait one frame past it.
    let inner = 0;
    const outer = requestAnimationFrame(() => { inner = requestAnimationFrame(() => (focus === "resend" ? resendRef : focus === "change_recipient" ? changeRef : message).current?.focus()); });
    return () => { cancelAnimationFrame(outer); cancelAnimationFrame(inner); };
  }, [focus, attempt, step]);

  const failureText = failed.length ? `Not delivered: ${failed.map(r => personLabel(r.recipient_name, r.recipient_email)).join(", ")}.` : null;
  const outcome = failed.length ? "The email did not reach every recipient." : current.length && current.every(r => r.result === "delivered") ? "The email was delivered." : "The email is on its way. Results appear here.";

  return <section className="req-page po-page po-issue" aria-label="Purchase order issued">
    <header><h2 className="po-number">{po.number} Issued</h2><p className="req-sub">{back}</p></header>
    {motion.reduced && <p className="req-sub">Animation is off: this is the finished picture.</p>}
    <IssueStage step={step} play={motion.play} onStep={setStep} failed={failed.length > 0} number={po.number} version={po.revision + 1} vendor={vendor} failure={failureText} />
    {step >= 3 && <div className="po-result">
      <h3>Result</h3>
      <p role="status">{outcome}{capped && " Still sending — results will appear on the order."}</p>
      <table className="po-lines"><caption className="visually-hidden">Result of the current send</caption><thead><tr><th scope="col">Recipient</th><th scope="col">Version</th><th scope="col">Result</th></tr></thead>
        <tbody>{current.map(r => <tr key={r.id}><td><Person name={r.recipient_name} email={r.recipient_email} /></td><td>v{po.revision + 1}</td><td><Signal tone={sentSignal(r).tone} label={sentSignal(r).label} /></td></tr>)}</tbody></table>
      {plan && <div className="po-recovery">
        <p ref={message} tabIndex={-1} role="alert">{failureText}{plan.focus === "message" && " The vendor's address reported this email as spam. Someone who can view this vendor's contacts can change the recipient."}</p>
        <div className="po-actions">
          {plan.actions.map(a => a === "resend"
            ? <button key={a} type="button" ref={resendRef} onClick={() => void resend()} aria-busy={busy || undefined} disabled={busy}>{busy ? "Resending…" : "Resend"}</button>
            : <ChangeRecipient key={a} ref={changeRef} poId={id} vendorId={po.vendor_id} onSent={sent} />)}
        </div>
        {plan.actions.includes("resend") && <p className="req-sub">Sends the same version again. Nothing more is committed.</p>}
        {refusal && <p role="alert" className="field-error">{refusal}</p>}
      </div>}
    </div>}
  </section>;
}
