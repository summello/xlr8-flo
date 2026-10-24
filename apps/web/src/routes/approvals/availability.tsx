import { CheckCircle, WarningCircle } from "@phosphor-icons/react";
import * as Dialog from "@radix-ui/react-dialog";
import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";

import { apiClient } from "../../api/client";
import type { components } from "../../api/generated/schema";
import { writeHeaders } from "../../features/auth/api";
import {
  buildDelegation,
  validateAvailability,
  type AvailabilityForm,
  type Problem,
} from "../../components/approvals/availability-model";
import InboxStatus from "../../components/approvals/InboxStatus";
import type { StatusTone } from "../../components/approvals/inbox-model";
import Person, { personLabel } from "../../components/ui/Person";
import "../../styles/approvals.css";

type Delegation = components["schemas"]["DelegationRead"];
type Picked = components["schemas"]["PickerRow"];
const STATE_WORDS: Record<Delegation["state"], string> = { active: "Active", scheduled: "Upcoming", ended: "Ended", revoked: "Revoked" };
const STATE_TONES: Record<Delegation["state"], StatusTone> = { active: "success", scheduled: "info", ended: "neutral", revoked: "danger" };
const blank: AvailabilityForm = { starts: "", ends: "", delegate: "", reason: "Out of office", covers: "all" };

export default function Availability({ navigate }: { navigate: (href: string) => void }) {
  const [tab, setTab] = useState<"mine" | "organization">(() => new URLSearchParams(window.location.search).get("view") === "organization" ? "organization" : "mine");
  const [organizationFailed, setOrganizationFailed] = useState(false);
  const [form, setForm] = useState<AvailabilityForm>(blank);
  const [problems, setProblems] = useState<Problem[]>([]);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState("");
  const [mine, setMine] = useState<Delegation[]>();
  const [covering, setCovering] = useState<Delegation[]>([]);
  const [organization, setOrganization] = useState<Delegation[]>();
  const [loadFailed, setLoadFailed] = useState(false);
  const [people, setPeople] = useState<Picked[]>([]);
  const [peopleFailed, setPeopleFailed] = useState(false);
  const [behalf, setBehalf] = useState<components["schemas"]["DecisionRead"][]>();
  const [behalfFailed, setBehalfFailed] = useState(false);
  const [reload, setReload] = useState(0);
  const [revoking, setRevoking] = useState<Delegation>();
  const [revokeError, setRevokeError] = useState("");
  const summary = useRef<HTMLDivElement>(null);
  const opener = useRef<HTMLElement | null>(null);
  const key = useRef(crypto.randomUUID());
  const set = (patch: Partial<AvailabilityForm>) => { setForm((f) => ({ ...f, ...patch })); key.current = crypto.randomUUID(); };

  useEffect(() => {
    let active = true;
    void Promise.all([
      apiClient.GET("/api/v1/approvals/delegations", { params: { query: { view: "mine" } } }),
      apiClient.GET("/api/v1/approvals/delegations", { params: { query: { view: "covering" } } }),
      apiClient.GET("/api/v1/approvals/delegations", { params: { query: { view: "organization" } } }),
    ]).then(([a, b, c]) => {
      if (!active) return;
      setLoadFailed(!a.data);
      if (a.data) setMine(a.data.rows);
      setCovering(b.data?.rows ?? []);
      setOrganizationFailed(!c.data);
      setOrganization(c.data?.rows); // refused for people without approval.admin: the tab stays hidden
    }).catch(() => { if (active) setLoadFailed(true); });
    return () => { active = false; };
  }, [reload]);

  useEffect(() => {
    let active = true;
    apiClient.GET("/api/v1/approvals/delegations/decided-on-my-behalf")
      .then((r) => { if (!active) return; setBehalfFailed(!r.data); setBehalf(r.data); })
      .catch(() => { if (active) setBehalfFailed(true); });
    return () => { active = false; };
  }, [reload]);

  // Eligibility is asked for what is covered: a colleague must hold that type.
  const selection = form.covers === "po" ? "po" : "";
  useEffect(() => {
    let active = true;
    const document_types = selection === "" ? undefined : (["po"] as "po"[]);
    void apiClient.GET("/api/v1/approvals/delegations/eligible-delegates", { params: { query: { document_types } } })
      .then((r) => { if (!active) return; setPeople(r.data?.rows ?? []); setPeopleFailed(!r.data); })
      .catch(() => { if (active) setPeopleFailed(true); });
    return () => { active = false; };
  }, [selection, reload]);

  const fail = useCallback((list: Problem[]) => { setProblems(list); setSaved(""); queueMicrotask(() => summary.current?.focus()); }, []);

  async function save(event: FormEvent) {
    event.preventDefault();
    const found = validateAvailability(form);
    if (found.length > 0) return fail(found);
    setSaving(true);
    try {
      const response = await apiClient.POST("/api/v1/approvals/delegations", {
        headers: { ...writeHeaders(), "Idempotency-Key": key.current },
        body: buildDelegation(form),
      });
      if (response.data) {
        setProblems([]);
        setSaved(`Saved. Approvals pass to ${personLabel(response.data.delegate_name, response.data.delegate_email)}.`);
        setForm(blank);
        key.current = crypto.randomUUID();
        setReload((n) => n + 1);
        return;
      }
      const error = response.error;
      const overlap = error?.checks?.problem === "delegation_overlaps";
      const mapped: Problem[] = (error?.errors ?? []).map((e) => ({
        category: e.field === "starts_at" || e.field === "ends_at" ? "Dates" : "Required field",
        field: e.field === "delegate_id" ? "delegate" : e.field === "ends_at" ? "ends" : e.field === "starts_at" ? "starts" : e.field === "reason" ? "reason" : "delegate",
        message: e.message,
      }));
      if (overlap) mapped.push({ category: "Overlap", field: "starts", message: error?.detail ?? "You already have a delegation in this period." });
      if (mapped.length === 0) mapped.push({ category: "Not saved", field: "delegate", message: `${error?.detail ?? "The server refused the request."} Your entries are kept.` });
      fail(mapped);
    } catch {
      fail([{ category: "Not saved", field: "delegate", message: "The server could not be reached. Your entries are kept." }]);
    } finally {
      setSaving(false);
    }
  }

  async function revoke(delegation: Delegation) {
    setRevokeError("");
    try {
      const response = await apiClient.POST("/api/v1/approvals/delegations/{id}/revoke", {
        params: { path: { id: delegation.id } },
        headers: writeHeaders(),
      });
      if (response.data) { setRevoking(undefined); setReload((n) => n + 1); }
      else setRevokeError(`Not revoked. ${response.error?.detail ?? "The server refused the request."} The delegation is unchanged. Try again.`);
    } catch {
      setRevokeError("Not revoked. The server could not be reached. The delegation is unchanged. Try again.");
    }
  }

  const fieldError = (field: Problem["field"]) => problems.find((p) => p.field === field)?.message;
  const rows = tab === "mine" ? mine : organization;

  return (
    <section className="appr" aria-label="Approval availability">
      <header className="appr-head">
        <div>
          <p className="appr-sub">Pass your approvals to a colleague while you are away.</p>
        </div>
        <a className="appr-button" href="/approvals" onClick={(event) => { event.preventDefault(); navigate("/approvals"); }}>Back to Inbox</a>
      </header>
      <div className="appr-grid">
        <div>
          <form className="appr-card" noValidate onSubmit={(event) => void save(event)} aria-labelledby="avail-form-title">
            <h2 id="avail-form-title" className="appr-card-title">Out-of-Office</h2>
            {problems.length > 0 && (
              <div ref={summary} role="alert" tabIndex={-1} className="appr-refusal">
                <p><WarningCircle aria-hidden="true" /> Not saved. {problems.length} {problems.length === 1 ? "problem" : "problems"}.</p>
                <ul>{problems.map((p, i) => <li key={`${p.field}-${i}`}><a href={`#avail-${p.field}`} onClick={(event) => { event.preventDefault(); document.getElementById(`avail-${p.field}`)?.focus(); }}>{p.category}: {p.message}</a></li>)}</ul>
              </div>
            )}
            {saved && <p role="status" className="appr-note"><CheckCircle aria-hidden="true" />{saved}</p>}
            <p className="appr-meta">{[form.starts, form.ends, form.delegate].some((v) => v === "") ? "Starts, Ends and Pass Approvals To are required and some are still empty." : "All required fields are filled."}</p>
            <div className="appr-field">
              <label htmlFor="avail-starts">Starts <span className="appr-tag">Required</span></label>
              <input id="avail-starts" type="date" value={form.starts} aria-invalid={fieldError("starts") ? true : undefined} aria-describedby={fieldError("starts") ? "avail-starts-error" : undefined} onChange={(e) => set({ starts: e.target.value })} />
              {fieldError("starts") && <p id="avail-starts-error" className="field-error"><WarningCircle aria-hidden="true" weight="regular" /><span>{fieldError("starts")}</span></p>}
            </div>
            <div className="appr-field">
              <label htmlFor="avail-ends">Ends <span className="appr-tag">Required</span></label>
              <input id="avail-ends" type="date" value={form.ends} aria-invalid={fieldError("ends") ? true : undefined} aria-describedby={fieldError("ends") ? "avail-ends-error" : "avail-ends-hint"} onChange={(e) => set({ ends: e.target.value })} />
              <p id="avail-ends-hint" className="appr-meta">The whole last day is included, in your browser's time zone.</p>
              {fieldError("ends") && <p id="avail-ends-error" className="field-error"><WarningCircle aria-hidden="true" weight="regular" /><span>{fieldError("ends")}</span></p>}
            </div>
            <div className="appr-field">
              <label htmlFor="avail-delegate">Pass Approvals To <span className="appr-tag">Required</span></label>
              <select id="avail-delegate" value={form.delegate} aria-invalid={fieldError("delegate") ? true : undefined} aria-describedby={fieldError("delegate") ? "avail-delegate-error" : "avail-delegate-hint"} onChange={(e) => set({ delegate: e.target.value })}>
                <option value="">Choose a colleague</option>
                {people.map((p) => <option key={p.user_id} value={p.user_id}>{personLabel(p.name, p.email)}</option>)}
              </select>
              <p id="avail-delegate-hint" className="appr-meta">{peopleFailed ? "Eligible people could not load. Reload the page to try again." : "Only people who may approve what you cover are listed. The server checks again on save."}</p>
              {fieldError("delegate") && <p id="avail-delegate-error" className="field-error"><WarningCircle aria-hidden="true" weight="regular" /><span>{fieldError("delegate")}</span></p>}
            </div>
            <fieldset className="appr-field">
              <legend>Covers</legend>
              <label><input type="radio" name="avail-covers" checked={form.covers === "all"} onChange={() => set({ covers: "all" })} /> All my approvals</label>
              <label><input type="radio" name="avail-covers" checked={form.covers === "po"} onChange={() => set({ covers: "po" })} /> Purchase orders only</label>
            </fieldset>
            <div className="appr-field">
              <label htmlFor="avail-reason">Reason</label>
              <input id="avail-reason" value={form.reason} aria-invalid={fieldError("reason") ? true : undefined} onChange={(e) => set({ reason: e.target.value })} />
              {fieldError("reason") && <p className="field-error"><WarningCircle aria-hidden="true" weight="regular" /><span>{fieldError("reason")}</span></p>}
            </div>
            <footer className="appr-actions"><button type="submit" className="appr-primary" aria-busy={saving}>Save Out-of-Office</button></footer>
          </form>

          <div className="appr-card">
            <div role="tablist" aria-label="Delegations" className="appr-tabs">
              <button role="tab" type="button" aria-selected={tab === "mine"} tabIndex={tab === "mine" ? 0 : -1} onClick={() => setTab("mine")}>My Delegations</button>
              {organization !== undefined && <button role="tab" type="button" aria-selected={tab === "organization"} tabIndex={tab === "organization" ? 0 : -1} onClick={() => setTab("organization")}>Organization</button>}
            </div>
            <div role="tabpanel">
              {tab === "organization" && organizationFailed ? (
                <div role="alert" className="appr-state"><h3>Could not load organization delegations</h3><p>You may not have access to this view. No organization data is shown.</p><button type="button" onClick={() => setTab("mine")}>Back to My Delegations</button></div>
              ) : loadFailed ? (
                <div role="alert" className="appr-state"><h3>Could not load your delegations</h3><p>Anything you typed above is kept.</p><button type="button" onClick={() => setReload((n) => n + 1)}>Try again</button></div>
              ) : rows === undefined ? (
                <p role="status">Loading delegations</p>
              ) : rows.length === 0 ? (
                <div className="appr-state"><h3>No Delegations</h3><p>When you pass your approvals on, the period is listed here.</p></div>
              ) : (
                <table className="appr-table">
                  <caption className="visually-hidden">{tab === "mine" ? "Your delegations" : "Organization delegations"}</caption>
                  <thead><tr><th scope="col">Delegate</th><th scope="col">Period</th><th scope="col">Covers</th><th scope="col">Status</th><th scope="col">Action</th></tr></thead>
                  <tbody>
                    {rows.map((d) => (
                      <tr key={d.id}>
                        <th scope="row">{tab === "organization" ? <><Person name={d.delegator_name} email={d.delegator_email} /> to <Person name={d.delegate_name} email={d.delegate_email} /></> : <Person name={d.delegate_name} email={d.delegate_email} />}</th>
                        <td data-label="Period"><time dateTime={d.starts_at}>{formatDay(d.starts_at)}</time> to <time dateTime={d.ends_at}>{formatDay(d.ends_at)}</time></td>
                        <td data-label="Covers">{d.document_types ? (d.document_types.join() === "po" ? "Purchase orders only" : d.document_types.join(", ")) : "All my approvals"}</td>
                        <td data-label="Status"><InboxStatus word={STATE_WORDS[d.state]} tone={STATE_TONES[d.state]} /></td>
                        <td data-label="Action">{d.state === "active" || d.state === "scheduled" ? <button type="button" onClick={(event) => { opener.current = event.currentTarget; setRevokeError(""); setRevoking(d); }}>Revoke<span className="visually-hidden"> delegation to {personLabel(d.delegate_name, d.delegate_email)}</span></button> : <span className="appr-meta">Kept in the record</span>}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          </div>
          <section className="appr-card" aria-labelledby="avail-behalf">
            <h2 id="avail-behalf" className="appr-card-title">Decided on Your Behalf</h2>
            {behalfFailed ? (
              <div role="alert" className="appr-state"><h3>Could not load these decisions</h3><p>Nothing was changed.</p><button type="button" onClick={() => setReload((n) => n + 1)}>Try again</button></div>
            ) : behalf === undefined ? (
              <p role="status">Loading decisions</p>
            ) : behalf.length === 0 ? (
              <p>No one has decided anything on your behalf.</p>
            ) : (
              <table className="appr-table">
                <caption className="visually-hidden">Decisions made on your behalf</caption>
                <thead><tr><th scope="col">Decided by</th><th scope="col">Step</th><th scope="col">Decision</th><th scope="col">When</th></tr></thead>
                <tbody>
                  {behalf.map((d) => (
                    <tr key={d.id}>
                      <th scope="row"><Person name={d.actor_name} email={d.actor_email} /></th>
                      <td data-label="Step">{d.step_label ?? "Request"}</td>
                      <td data-label="Decision">{d.outcome}{d.comment && <span className="appr-meta">{d.comment}</span>}</td>
                      <td data-label="When"><time dateTime={d.decided_at}>{formatDay(d.decided_at)}</time></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </section>
        </div>
        <aside className="appr-side">
          <section className="appr-card"><h2 className="appr-card-title">Eligible Delegates</h2><p>{people.length === 0 ? "No one else may approve everything you cover. Narrow what you cover or ask an administrator." : `${people.length} ${people.length === 1 ? "person is" : "people are"} eligible. Anyone missing cannot approve everything you cover.`}</p></section>
          <section className="appr-card"><h2 className="appr-card-title">Covering for Others</h2>
            {covering.length === 0 ? <p>You are not covering for anyone. Even when you are, you cannot decide your own requests.</p> : <ul>{covering.map((d) => <li key={d.id}><Person name={d.delegator_name} email={d.delegator_email} /> until <time dateTime={d.ends_at}>{formatDay(d.ends_at)}</time> ({STATE_WORDS[d.state]})</li>)}</ul>}
          </section>
          <section className="appr-card"><h2 className="appr-card-title">Reminders and Escalation</h2><p>Reminders and escalation are set per step in the workflow builder. While a delegation is active, reminders go to the delegate and the due date does not move.</p></section>
        </aside>
      </div>
      <Dialog.Root open={revoking !== undefined} onOpenChange={(open) => { if (!open) setRevoking(undefined); }}>
        <Dialog.Portal>
          <Dialog.Overlay className="appr-scrim" />
          <Dialog.Content role="alertdialog" className="appr-dialog" onCloseAutoFocus={(event) => { event.preventDefault(); if (opener.current?.isConnected) opener.current.focus(); }} onOpenAutoFocus={(event) => { event.preventDefault(); document.getElementById("avail-keep")?.focus(); }}>
            <Dialog.Title>Revoke this delegation?</Dialog.Title>
            <Dialog.Description>
              New approvals stop going to {revoking && <Person name={revoking.delegate_name} email={revoking.delegate_email} />}. Tasks still undecided come back to you. Decisions already made stay recorded as made by {revoking && <Person name={revoking.delegate_name} email={revoking.delegate_email} />}, acting for you.
            </Dialog.Description>
            {revokeError && <p role="alert" className="field-error"><WarningCircle aria-hidden="true" weight="regular" /><span>{revokeError}</span></p>}
            <footer className="appr-actions">
              <Dialog.Close id="avail-keep">Keep Delegation</Dialog.Close>
              <button type="button" className="appr-danger" onClick={() => revoking && void revoke(revoking)}>Revoke Delegation</button>
            </footer>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </section>
  );
}

function formatDay(value: string): string {
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium" }).format(new Date(value));
}
