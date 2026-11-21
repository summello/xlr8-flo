import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { apiClient } from "../../api/client";
import type { components } from "../../api/generated/schema";
import { writeHeaders } from "../../features/auth/api";
import Combobox, { type ComboboxOption } from "../form/Combobox";
import ErrorSummary, { type SummaryError } from "../form/ErrorSummary";
import Field from "../form/Field";
import Input from "../form/Input";
import MoneyInput from "../form/MoneyInput";
import Money from "../ui/Money";
import Picker, { type PickerOption } from "./Picker";
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
const fieldLabels: Record<string, string> = { budget: "Budget", parent_id: "Main project", sponsor_ids: "Sponsors" };

export default function ProjectForm({ onCreated }: { onCreated: (id: string) => void }) {
  const [values, setValues] = useState<Record<Key, string>>({ bu_id: "", name: "", department_code: "", ledger_account_code: "", currency: "", description: "", planned_start: "", planned_end: "" });
  const [options, setOptions] = useState<Partial<Record<Key, ComboboxOption[]>>>({});
  const [errors, setErrors] = useState<SummaryError[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [retry, setRetry] = useState(0);
  const [busy, setBusy] = useState(false);
  const [budget, setBudget] = useState("");
  const [parentId, setParentId] = useState("");
  const [parentOption, setParentOption] = useState<PickerOption[]>([]);
  const [sponsors, setSponsors] = useState<PickerOption[]>([]);
  const [mainFunds, setMainFunds] = useState<{ id: string; available: string; currency: string } | { id: string; unknown: true } | null>(null);
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
  const unit = values.bu_id;
  const noUnit = /^[0-9a-f-]{36}$/i.test(unit) ? undefined : "Choose a business unit first.";
  const lookup = useCallback(async (kind: "main_projects" | "people", q: string): Promise<PickerOption[]> => {
    if (!/^[0-9a-f-]{36}$/i.test(unit)) return [];
    const { data } = await apiClient.GET("/api/v1/projects/lookups/{kind}", { params: { path: { kind }, query: { bu_id: unit, q, limit: 20 } } });
    if (!data) throw new Error("lookup failed");
    return data.rows;
  }, [unit]);
  const loadMains = useCallback((q: string) => lookup("main_projects", q), [lookup]);
  const loadPeople = useCallback((q: string) => lookup("people", q), [lookup]);
  useEffect(() => {
    if (!parentId) return;
    const controller = new AbortController();
    void apiClient.GET("/api/v1/projects/{project_id}/balance", { signal: controller.signal, params: { path: { project_id: parentId } } })
      .then(result => { if (!controller.signal.aborted) setMainFunds(result.data ? { id: parentId, available: result.data.available, currency: result.data.currency } : { id: parentId, unknown: true }); })
      .catch(() => { if (!controller.signal.aborted) setMainFunds({ id: parentId, unknown: true }); });
    return () => controller.abort();
  }, [parentId]);
  const funds = mainFunds?.id === parentId ? mainFunds : null;
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const missing = fields.filter(([key, , , required]) => required && !values[key].trim());
    if (missing.length) { setErrors(missing.map(([key, label]) => ({ fieldId: `project-${key}`, label, message: "Complete this required field." }))); return; }
    const body: Create = {
      bu_id: values.bu_id, name: values.name, department_code: values.department_code,
      ledger_account_code: values.ledger_account_code, currency: values.currency,
      description: values.description || null, planned_start: values.planned_start || null, planned_end: values.planned_end || null,
      parent_id: parentId || null, sponsor_ids: sponsors.map(o => o.value),
      budget: budget.trim() ? { amount: budget.trim(), currency: values.currency } : null,
    };
    setBusy(true);
    try {
      const result = await apiClient.POST("/api/v1/projects", { body, headers: writeHeaders() });
      if (result.data) { onCreated(result.data.id); return; }
      setErrors(result.error?.errors?.length ? result.error.errors.map(error => ({
        fieldId: `project-${error.field === "budget" ? "budget_amount" : error.field}`, label: fields.find(([key]) => key === error.field)?.[1] ?? fieldLabels[error.field] ?? error.field, message: error.message,
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
    <Field id="project-budget_amount" label="Budget" help="The budget this project asks for, in the project currency. It is allocated when the project is approved. Required to submit, optional for a draft." error={errors.find(e => e.fieldId === "project-budget_amount")?.message}>
      <MoneyInput autoComplete="off" currency={values.currency || "—"} value={budget} onChange={e => setBudget(e.target.value)} />
    </Field>
    <Field id="project-parent_id" label="Main project" help="Optional. Choose a main project to draw this project's budget from; leave empty for a main project of its own." error={errors.find(e => e.fieldId === "project-parent_id")?.message}>
      <Picker blocked={noUnit} load={loadMains} selected={parentOption} max={1} noun="main projects" onChange={next => { setParentOption(next); setParentId(next[0]?.value ?? ""); }} />
    </Field>
    {parentId && <p role="status" className="field-help" data-testid="funds-check">
      {funds && "unknown" in funds ? "The main project's available funds are not shown to you. They are checked again when the project is approved."
        : funds ? <>Available in the main project: <Money value={funds.available} currency={funds.currency} />
. The server checks the budget against it when you create the project and again when it is approved.</>
        : "Checking the main project's available funds…"}</p>}
    <Field id="project-sponsor_ids" label="Sponsors" help="Choose one to ten people who sponsor the project. Search by email and add each person. Required to submit." error={errors.find(e => e.fieldId === "project-sponsor_ids")?.message}>
      <Picker blocked={noUnit} load={loadPeople} selected={sponsors} max={10} noun="sponsors" person onChange={setSponsors} />
    </Field>
    <button className="form-submit" disabled={busy} type="submit">{busy ? "Creating project…" : "Create project"}</button>
  </form>;
}
