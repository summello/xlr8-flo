import { Fragment, useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { apiClient } from "../../api/client";
import { failure, formatTime, mutationHeaders } from "../requisitions/api";
import MaskedValue from "../ui/MaskedValue";
import type { components } from "../../api/generated/schema";

type Masked = components["schemas"]["SensitiveMasked"];
type Values = components["schemas"]["SensitiveValues"];
type Access = components["schemas"]["SensitiveAccess"];

type Problem = { detail?: string | null; errors?: { field: string; message: string }[] | null };
/** The API answers 422 with a generic detail and the real message in errors[]; pydantic prefixes validator text. */
function fieldErrors(error: Problem | undefined): Record<string, string> {
  return Object.fromEntries((error?.errors ?? []).map(e => [e.field, e.message.replace(/^Value error, /, "")]));
}

/** Open question E11-S08 #3 (decision 221 settled 60 s); change here only. */
export const REVEAL_SECONDS = 60;

const REASONS = [
  { code: "verify_payment", label: "Verify a payment" },
  { code: "vendor_request", label: "Vendor asked" },
  { code: "audit_support", label: "Audit support" },
  { code: "other", label: "Other" },
] as const;

const EDIT_FIELDS = [["tax_id", "Tax ID"], ["tax_id_country", "Tax ID country"], ["bank_name", "Bank"], ["bank_account", "Account number"], ["bank_routing", "Routing code"]] as const;

/** Tax and Banking (VEN-014, CUS-008). Revealed values live only in this component's state: re-masking drops them, it does not hide them. */
export default function TaxBankingCard({ vendorId, masked, access, onSaved }: { vendorId: string; masked: Masked; access: Access; onSaved: () => void }) {
  const [asking, setAsking] = useState(false);
  const [reason, setReason] = useState("");
  const [note, setNote] = useState("");
  const [errors, setErrors] = useState<{ reason?: string; note?: string; form?: string }>({});
  const [busy, setBusy] = useState(false);
  const [shown, setShown] = useState<{ values: Values; at: Date } | null>(null);
  const card = useRef<HTMLDivElement>(null);
  const [edit, setEdit] = useState<Record<string, string> | null>(null);
  const [editErrors, setEditErrors] = useState<Record<string, string>>({});
  const remask = useCallback(() => { setShown(null); setEdit(null); setEditErrors({}); }, []);

  useEffect(() => {
    if (!shown) return;
    const timer = window.setTimeout(remask, REVEAL_SECONDS * 1000);
    return () => window.clearTimeout(timer);
  }, [shown, remask]);
  // The form that held focus is gone once values show; keep focus inside the card so Tab out re-masks.
  useEffect(() => { if (shown) card.current?.focus(); }, [shown]);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!reason) { setErrors({ reason: "Choose a reason. It is logged with the reveal." }); return; }
    setBusy(true); setErrors({});
    const { data, error, response } = await apiClient.POST("/api/v1/vendors/{id}/sensitive/reveal", {
      params: { path: { id: vendorId } },
      headers: mutationHeaders(crypto.randomUUID()),
      body: { fields: ["tax_id", "bank"], reason_code: reason as (typeof REASONS)[number]["code"], note: note.trim() || null },
    });
    setBusy(false);
    if (!data) {
      const problem = error as Problem | undefined;
      const fields = fieldErrors(problem);
      const message = failure(problem ?? undefined, "Revealing the values");
      if (response?.status === 422 && (fields.note || fields.reason_code)) setErrors({ note: fields.note, reason: fields.reason_code });
      else setErrors({ form: message });
      return;
    }
    setShown({ values: data, at: new Date() });
    setAsking(false); setReason(""); setNote("");
  };

  const startEdit = () => {
    const x = shown!.values;
    setEdit({ tax_id: x.tax_id ?? "", tax_id_country: x.tax_id_country ?? "", bank_name: x.bank_name ?? "", bank_account: x.bank_account ?? "", bank_routing: x.bank_routing ?? "" });
  };
  const save = async (event: FormEvent) => {
    event.preventDefault();
    if (!edit || !shown) return;
    setBusy(true); setEditErrors({});
    const body = Object.fromEntries(Object.entries(edit).map(([k, value]) => [k, value.trim() || null]));
    const { data, error, response } = await apiClient.PUT("/api/v1/vendors/{id}/sensitive", {
      params: { path: { id: vendorId } },
      headers: mutationHeaders(crypto.randomUUID(), shown.values.version),
      body,
    });
    setBusy(false);
    if (!data) {
      const problem = error as Problem | undefined;
      const fields = fieldErrors(problem);
      setEditErrors(response?.status === 422 && Object.keys(fields).length ? fields : { form: failure(problem ?? undefined, "Saving the values") });
      return;
    }
    remask(); onSaved();
  };

  const v = shown?.values;
  const rows: [string, string | null | undefined, string | null | undefined][] = [
    ["Tax ID", masked.tax_id, v?.tax_id],
    ["Tax ID country", masked.tax_id_country, v?.tax_id_country],
    ["Bank", masked.bank_name, v?.bank_name],
    ["Account number", masked.bank_account, v?.bank_account],
    ["Routing code", masked.bank_routing, v?.bank_routing],
  ];

  return <div ref={card} className="tax-card" tabIndex={-1}
    onPointerLeave={shown ? remask : undefined}
    onBlur={shown ? e => { if (!card.current?.contains(e.relatedTarget as Node | null)) remask(); } : undefined}>
    <dl className="vendor-facts">{rows.map(([name, hidden, clear]) => <Fragment key={name}><dt>{name}</dt><dd><MaskedValue value={shown ? clear : hidden} /></dd></Fragment>)}</dl>
    {shown && <p className="req-sub" role="status">Revealed by you, {formatTime(shown.at)}. Logged.</p>}
    {shown && access.can_edit && !edit && <button type="button" onClick={startEdit}>Edit</button>}
    {shown && edit && <form className="tax-reveal" onSubmit={save} noValidate>
      {editErrors.form && <p role="alert">{editErrors.form}</p>}
      {EDIT_FIELDS.map(([key, name]) => <div key={key} className="tax-field">
        <label htmlFor={`edit-${key}`}>{name}</label>
        <input id={`edit-${key}`} value={edit[key]} autoComplete="off" onChange={e => setEdit({ ...edit, [key]: e.target.value })} aria-invalid={editErrors[key] ? true : undefined} aria-describedby={editErrors[key] ? `edit-${key}-error` : undefined} />
        {editErrors[key] && <p id={`edit-${key}-error`} role="alert">{editErrors[key]}</p>}
      </div>)}
      <div className="tax-reveal-actions">
        <button type="submit" disabled={busy}>Save</button>
        <button type="button" onClick={() => { setEdit(null); setEditErrors({}); }}>Cancel</button>
      </div>
    </form>}
    {!access.can_reveal && <p><strong>Restricted</strong> <span className="req-sub">Ask {access.ask} to reveal these values.</span></p>}
    {access.can_reveal && !shown && !asking && <button type="button" onClick={() => setAsking(true)}>Reveal Values</button>}
    {access.can_reveal && !shown && asking && <form className="tax-reveal" onSubmit={submit} noValidate>
      {errors.form && <p role="alert">{errors.form}</p>}
      <label htmlFor="reveal-reason">Reason</label>
      <select id="reveal-reason" value={reason} onChange={e => setReason(e.target.value)} aria-required="true" aria-invalid={errors.reason ? true : undefined} aria-describedby={errors.reason ? "reveal-reason-error" : undefined}>
        <option value="">Choose a reason</option>
        {REASONS.map(r => <option key={r.code} value={r.code}>{r.label}</option>)}
      </select>
      {errors.reason && <p id="reveal-reason-error" role="alert">{errors.reason}</p>}
      <label htmlFor="reveal-note">Note (optional)</label>
      <input id="reveal-note" value={note} maxLength={200} onChange={e => setNote(e.target.value)} aria-invalid={errors.note ? true : undefined} aria-describedby={errors.note ? "reveal-note-error" : undefined} />
      {errors.note && <p id="reveal-note-error" role="alert">{errors.note}</p>}
      <div className="tax-reveal-actions">
        <button type="submit" disabled={busy}>Reveal Values</button>
        <button type="button" onClick={() => { setAsking(false); setErrors({}); }}>Cancel</button>
      </div>
    </form>}
  </div>;
}
