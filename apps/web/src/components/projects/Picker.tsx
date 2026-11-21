import { useEffect, useState } from "react";
import Input from "../form/Input";
import Person from "../ui/Person";

export type PickerOption = { value: string; label: string; disabled?: boolean; note?: string | null };

export type MatchState = "searching" | "blocked" | "failed" | "settled";

/** The match list for one lookup state (UX-004): never "No matches." until a search has settled. */
export function PickerMatches({ state, rows, noun, blocked, onAdd }: {
  state: MatchState;
  rows: PickerOption[];
  noun: string;
  blocked?: string;
  onAdd: (option: PickerOption) => void;
}) {
  if (state === "blocked") return <p role="status" className="field-help">{blocked}</p>;
  if (state === "searching") return <p role="status" className="field-help">Searching…</p>;
  if (state === "failed") return null;
  return <ul className="picker-list" aria-label={`Matching ${noun}`}>
    {rows.map(o => <li key={o.value}><button type="button" disabled={o.disabled} onClick={() => onAdd(o)}>
      Add {o.label}{o.note ? ` (${o.note})` : ""}</button></li>)}
    {!rows.length && <li className="field-help">No matches.</li>}
  </ul>;
}

/** A server-searched picker: type to search, choose from the matches, remove a choice by name. */
export default function Picker({ load, selected, onChange, max, noun, blocked, person }: {
  load: (query: string) => Promise<PickerOption[]>;
  selected: PickerOption[];
  onChange: (next: PickerOption[]) => void;
  max: number;
  noun: string;
  /** Why the lookup cannot run yet (for example no business unit); shown instead of results. */
  blocked?: string;
  /** Chosen rows are people: the label is the name and the note the email (E22-S09). */
  person?: boolean;
}) {
  const [query, setQuery] = useState("");
  // The settled result remembers the query and loader it answers, so "searching" is derived
  // (no state is set synchronously inside the effect) and a changed query or unit reads as loading.
  const [result, setResult] = useState<{ query: string; load: typeof load; rows: PickerOption[]; failed: boolean } | null>(null);
  useEffect(() => {
    if (blocked) return;
    let live = true;
    const timer = setTimeout(() => {
      load(query).then(rows => { if (live) setResult({ query, load, rows, failed: false }); })
        .catch(() => { if (live) setResult({ query, load, rows: [], failed: true }); });
    }, 250);
    return () => { live = false; clearTimeout(timer); };
  }, [load, query, blocked]);
  const current = result?.query === query && result.load === load ? result : null;
  const state: MatchState = blocked ? "blocked" : !current ? "searching" : current.failed ? "failed" : "settled";
  const failed = state === "failed";
  const full = selected.length >= max;
  const open = (current?.rows ?? []).filter(o => !selected.some(s => s.value === o.value));
  return <div className="picker">
    <Input autoComplete="off" inputMode="search" type="search" value={query} placeholder={`Search ${noun}`} onChange={e => setQuery(e.target.value)} />
    {failed && <p role="alert">The {noun} list could not load. Your choices are kept; change the search to retry.</p>}
    {full && <p role="status">You have named {max} {noun}, the most allowed. Remove one to add another.</p>}
    {!full && <PickerMatches state={state} rows={open} noun={noun} blocked={blocked} onAdd={o => onChange([...selected, o])} />}
    <ul className="picker-list" aria-label={`Chosen ${noun}`}>
      {selected.map(o => <li key={o.value}>{person && o.note ? <Person name={o.label} email={o.note} /> : o.label} <button type="button" onClick={() => onChange(selected.filter(s => s.value !== o.value))}>Remove {o.label}</button></li>)}
      {!selected.length && <li className="field-help">None chosen.</li>}
    </ul>
  </div>;
}
