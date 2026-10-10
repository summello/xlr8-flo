import { useEffect, useRef, useState, type FormEvent } from "react";
import { apiClient } from "../../api/client";
import type { components } from "../../api/generated/schema";
import { writeHeaders } from "../../features/auth/api";
import Combobox, { type ComboboxOption } from "../form/Combobox";
import ErrorSummary, { type SummaryError } from "../form/ErrorSummary";
import Field from "../form/Field";
import Input from "../form/Input";
import { failure } from "./api";

type Create = components["schemas"]["ProjectCreate"];
const fields = [
  ["bu_id", "Business unit", "Choose an active business or organizational unit.", true],
  ["name", "Project name", "Give the project a clear name.", true],
  ["department_code", "Department", "Search active department codes.", true],
  ["ledger_account_code", "Ledger account", "Search active ledger account codes.", true],
  ["currency", "Currency", "Choose the project's ISO currency code. This cannot change later.", true],
  ["description", "Description", "Describe the purpose and scope.", false],
  ["planned_start", "Planned start", "Optional planned start date.", false],
  ["planned_end", "Planned end", "Optional planned end date.", false],
] as const;
type Key = typeof fields[number][0];

export default function ProjectForm({ onCreated }: { onCreated: (id: string) => void }) {
  const [values, setValues] = useState<Record<Key, string>>({ bu_id: "", name: "", department_code: "", ledger_account_code: "", currency: "", description: "", planned_start: "", planned_end: "" });
  const [options, setOptions] = useState<Partial<Record<Key, ComboboxOption[]>>>({});
  const [errors, setErrors] = useState<SummaryError[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [retry, setRetry] = useState(0);
  const [busy, setBusy] = useState(false);
  const summary = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!errors.length) return;
    const target = summary.current?.querySelector<HTMLElement>("[role=alert]");
    if (target) { target.tabIndex = -1; target.focus(); }
  }, [errors]);
  useEffect(() => {
    const controller = new AbortController();
    const signal = controller.signal;
    void Promise.all([
      apiClient.GET("/api/v1/org/units", { signal, params: { query: { q: values.bu_id, page_size: 50 } } }),
      apiClient.GET("/api/v1/master/{kind}", { signal, params: { path: { kind: "department" }, query: { q: values.department_code, page_size: 50, active: true } } }),
      apiClient.GET("/api/v1/master/{kind}", { signal, params: { path: { kind: "ledger_account" }, query: { q: values.ledger_account_code, page_size: 50, active: true } } }),
      apiClient.GET("/api/v1/master/currency", { signal }),
    ]).then(([units, departments, ledger, currencies]) => {
      if (signal.aborted) return;
      setLoadError(null);
      setOptions(current => ({ ...current,
        bu_id: units.data?.rows.filter(u => u.active).map(u => ({ value: u.id, label: u.name })),
        department_code: departments.data?.rows.map(r => ({ value: r.code, label: r.name })),
        ledger_account_code: ledger.data?.rows.map(r => ({ value: r.code, label: r.name })),
        currency: currencies.data?.map(c => ({ value: c.code, label: c.code })),
      }));
      if ([units, departments, ledger, currencies].some(r => !r.data)) setLoadError("Some suggestions could not load. Your entries are preserved; retry or enter a known code.");
    }).catch(() => { if (!signal.aborted) setLoadError("Suggestions could not load. Your entries are preserved; check your connection and retry."); });
    return () => controller.abort();
  }, [values.bu_id, values.department_code, values.ledger_account_code, retry]);
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const missing = fields.filter(([key, , , required]) => required && !values[key].trim());
    if (missing.length) { setErrors(missing.map(([key, label]) => ({ fieldId: `project-${key}`, label, message: "Complete this required field." }))); return; }
    const body: Create = {
      bu_id: values.bu_id, name: values.name, department_code: values.department_code,
      ledger_account_code: values.ledger_account_code, currency: values.currency,
      description: values.description || null, planned_start: values.planned_start || null, planned_end: values.planned_end || null,
    };
    setBusy(true);
    try {
      const result = await apiClient.POST("/api/v1/projects", { body, headers: writeHeaders() });
      if (result.data) { onCreated(result.data.id); return; }
      setErrors(result.error?.errors?.length ? result.error.errors.map(error => ({
        fieldId: `project-${error.field}`, label: fields.find(([key]) => key === error.field)?.[1] ?? error.field, message: error.message,
      })) : [{ fieldId: "project-name", label: "Project", message: failure(result.error, "Creating the project") }]);
    } catch { setErrors([{ fieldId: "project-name", label: "Project", message: failure(undefined, "Creating the project") }]); }
    finally { setBusy(false); }
  };
  return <form className="form-kit project-form material-surface" onSubmit={e => void submit(e)} noValidate>
    <p>Define the project before submitting it for approval. You become its owner.</p>
    <div ref={summary}><ErrorSummary errors={errors} /></div>
    {loadError && <div role="alert"><p>{loadError}</p><button type="button" onClick={() => setRetry(n => n + 1)}>Retry suggestions</button></div>}
    {fields.map(([key, label, help, required]) => <Field key={key} id={`project-${key}`} label={label} help={help} required={required} error={errors.find(e => e.fieldId === `project-${key}`)?.message}>
      {["bu_id", "department_code", "ledger_account_code", "currency"].includes(key)
        ? <Combobox autoComplete="off" inputMode="text" options={options[key] ?? []} value={values[key]} onChange={e => setValues(v => ({ ...v, [key]: e.target.value }))} />
        : <Input autoComplete="off" inputMode="text" type={key.startsWith("planned_") ? "date" : "text"} value={values[key]} onChange={e => setValues(v => ({ ...v, [key]: e.target.value }))} />}
    </Field>)}
    <button className="form-submit" disabled={busy} type="submit">{busy ? "Creating project…" : "Create project"}</button>
  </form>;
}
