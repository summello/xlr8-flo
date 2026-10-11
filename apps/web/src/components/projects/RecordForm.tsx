import { useEffect, useRef, useState, type FormEvent } from "react";
import ErrorSummary, { type SummaryError } from "../form/ErrorSummary";
import Field from "../form/Field";
import Select from "../form/Select";
import Input from "../form/Input";
import { failure, WriteFailure } from "./api";

export type RecordField = { key: string; label: string; help: string; type?: "text" | "date" | "number"; required?: boolean; options?: readonly string[]; min?: number; max?: number };
export default function RecordForm({ title, fields, initial = {}, save, cancel }: {
  title: string; fields: readonly RecordField[]; initial?: Record<string, string>;
  save: (values: Record<string, string>) => Promise<void>; cancel: () => void;
}) {
  const [values, setValues] = useState(initial);
  const [error, setError] = useState<SummaryError[]>([]);
  const [busy, setBusy] = useState(false);
  const summary = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!error.length) return;
    const target = summary.current?.querySelector<HTMLElement>("[role=alert]");
    if (target) { target.tabIndex = -1; target.focus(); }
  }, [error]);
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const invalid = fields.flatMap(field => {
      const value = values[field.key] ?? "";
      const missing = field.required && !value.trim();
      const badNumber = field.type === "number" && value && (!/^-?\d+$/.test(value) ||
        (field.min !== undefined && Number(value) < field.min) || (field.max !== undefined && Number(value) > field.max));
      return missing || badNumber ? [{ fieldId: `record-${field.key}`, label: field.label, message: missing ? "Complete this required field." : "Enter a whole number within the stated range." }] : [];
    });
    if (invalid.length) { setError(invalid); return; }
    setBusy(true); setError([]);
    try { await save(values); }
    catch (problem) {
      if (problem instanceof WriteFailure && problem.problem?.errors?.length) {
        setError(problem.problem.errors.map(e => ({ fieldId: `record-${e.field}`, label: fields.find(f => f.key === e.field)?.label ?? e.field, message: e.message })));
      } else setError([{ fieldId: `record-${fields[0]!.key}`, label: title, message: problem instanceof Error ? problem.message : failure(undefined, "Saving") }]);
    } finally { setBusy(false); }
  };
  return <form className="form-kit project-form" onSubmit={e => void submit(e)} noValidate>
    <h3>{title}</h3><div ref={summary}><ErrorSummary errors={error} /></div>
    {fields.map(field => <Field key={field.key} id={`record-${field.key}`} label={field.label} help={field.help} required={field.required} error={error.find(e => e.fieldId === `record-${field.key}`)?.message}>
      {field.options ? <Select id={`record-${field.key}`} value={values[field.key] ?? field.options[0]} required={field.required} onChange={e => setValues(v => ({ ...v, [field.key]: e.target.value }))}>{field.options.map(o => <option key={o} value={o}>{o.replaceAll("_", " ")}</option>)}</Select>
        : <Input autoComplete="off" inputMode={field.type === "number" ? "numeric" : "text"} type={field.type ?? "text"} required={field.required} min={field.min} max={field.max} value={values[field.key] ?? ""} onChange={e => setValues(v => ({ ...v, [field.key]: e.target.value }))} />}
    </Field>)}
    <div className="project-actions"><button disabled={busy} type="submit">{busy ? "Saving…" : "Save"}</button><button type="button" disabled={busy} onClick={cancel}>Cancel</button></div>
  </form>;
}
