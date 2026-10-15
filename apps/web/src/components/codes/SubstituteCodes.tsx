import * as Dialog from "@radix-ui/react-dialog";
import { ArrowsLeftRight } from "@phosphor-icons/react";
import { useEffect, useRef, useState } from "react";
import { apiClient } from "../../api/client";
import type { components } from "../../api/generated/schema";
import { writeHeaders } from "../../features/auth/api";
import "../../styles/requisitions.css";

type Codes = components["schemas"]["CodesRead"];
type Option = components["schemas"]["LookupOption"];
type Field = "department_code" | "ledger_account_code";
type Problem = { detail?: string | null; correlation_id?: string | null } | undefined;
const FIELDS: { field: Field; kind: "department" | "ledger_account"; name: string }[] = [
  { field: "department_code", kind: "department", name: "department" },
  { field: "ledger_account_code", kind: "ledger_account", name: "ledger account" },
];

/** Project Manager action: replace an expired code on a project or requisition (D-M2-25). */
export default function SubstituteCodes({ target, id, codes, onDone }: { target: "requisition" | "project"; id: string; codes: Codes; onDone: () => void }) {
  const [open, setOpen] = useState(false);
  const [values, setValues] = useState<Partial<Record<Field, string>>>({});
  const [reason, setReason] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [options, setOptions] = useState<Record<string, Option[]>>({});
  const key = useRef(crypto.randomUUID());
  const expired = FIELDS.filter(({ kind }) => codes[kind].status !== "usable");

  useEffect(() => {
    if (!open) return;
    for (const { kind } of expired) {
      void apiClient.GET("/api/v1/requisitions/lookups/{kind}", { params: { path: { kind }, query: { limit: 50 } } })
        .then(r => { if (r.data) setOptions(o => ({ ...o, [kind]: r.data!.rows })); });
    }
    // expired is derived from codes; reload lookups only when the dialog opens.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const submit = async () => {
    const body = { reason: reason.trim(), ...Object.fromEntries(expired.map(({ field }) => [field, values[field]?.trim()]).filter(([, v]) => v)) };
    if (!body.reason || expired.every(({ field }) => !values[field]?.trim())) {
      setError("Choose a replacement code and give a reason. Nothing was changed.");
      return;
    }
    setBusy(true); setError("");
    try {
      const init = { params: { path: { id } }, headers: { ...writeHeaders(), "Idempotency-Key": key.current }, body };
      const { data, error: problem } = target === "project"
        ? await apiClient.POST("/api/v1/projects/{id}/codes", init)
        : await apiClient.POST("/api/v1/requisitions/{id}/codes", init);
      if (data) { setOpen(false); key.current = crypto.randomUUID(); onDone(); return; }
      const detail = (problem as Problem)?.detail;
      setError(`${detail ?? "The codes could not be substituted."} Nothing was changed; your entries are kept. Correct the issue or try again.`);
    } catch {
      setError("The server could not be reached. Nothing was changed; your entries are kept. Try again.");
    } finally { setBusy(false); }
  };

  return <Dialog.Root open={open} onOpenChange={next => { if (!busy) setOpen(next); }}>
    <Dialog.Trigger className="req-link-button"><ArrowsLeftRight aria-hidden="true" weight="regular" />Substitute codes</Dialog.Trigger>
    <Dialog.Portal><Dialog.Overlay className="project-dialog-overlay" />
      <Dialog.Content className="project-dialog material-surface material-shadow req-dialog" data-shadow="lg">
        <Dialog.Title>Substitute codes</Dialog.Title>
        <Dialog.Description>Replace an expired code with a usable one. Past ledger entries keep the code they were posted under. The change is audited.</Dialog.Description>
        {expired.map(({ field, kind, name }) => <div className="req-field" key={field}>
          <label className="t-label" htmlFor={`sub-${field}`}>Replace {name} code {codes[kind].code} ({codes[kind].status.replaceAll("_", " ")})</label>
          <input id={`sub-${field}`} list={`sub-${field}-list`} autoComplete="off" value={values[field] ?? ""} onChange={e => setValues(v => ({ ...v, [field]: e.target.value.split(" ")[0] }))} />
          <datalist id={`sub-${field}-list`}>{(options[kind] ?? []).map(o => <option key={o.value} value={o.value}>{o.label}</option>)}</datalist>
        </div>)}
        <div className="req-field">
          <label className="t-label" htmlFor="sub-reason">Reason</label>
          <textarea id="sub-reason" rows={3} value={reason} onChange={e => setReason(e.target.value)} />
        </div>
        {error && <p role="alert" className="field-error">{error}</p>}
        <div className="req-actions">
          <Dialog.Close className="req-link-button" type="button">Cancel</Dialog.Close>
          <button type="button" className="form-submit" aria-busy={busy} onClick={() => void submit()}>Substitute</button>
        </div>
      </Dialog.Content>
    </Dialog.Portal>
  </Dialog.Root>;
}
