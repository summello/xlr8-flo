import { useEffect, useState } from "react";
import Input from "../form/Input";

export type PickerOption = { value: string; label: string; disabled?: boolean; note?: string | null };

/** A server-searched picker: type to search, choose from the matches, remove a choice by name. */
export default function Picker({ load, selected, onChange, max, noun }: {
  load: (query: string) => Promise<PickerOption[]>;
  selected: PickerOption[];
  onChange: (next: PickerOption[]) => void;
  max: number;
  noun: string;
}) {
  const [query, setQuery] = useState("");
  const [matches, setMatches] = useState<PickerOption[]>([]);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let live = true;
    const timer = setTimeout(() => {
      load(query).then(rows => { if (live) { setMatches(rows); setFailed(false); } })
        .catch(() => { if (live) setFailed(true); });
    }, 250);
    return () => { live = false; clearTimeout(timer); };
  }, [load, query]);
  const full = selected.length >= max;
  const open = matches.filter(o => !selected.some(s => s.value === o.value));
  return <div className="picker">
    <Input autoComplete="off" inputMode="search" type="search" value={query} placeholder={`Search ${noun}`} onChange={e => setQuery(e.target.value)} />
    {failed && <p role="alert">The {noun} list could not load. Your choices are kept; change the search to retry.</p>}
    {full && <p role="status">You have named {max} {noun}, the most allowed. Remove one to add another.</p>}
    {!full && <ul className="picker-list" aria-label={`Matching ${noun}`}>
      {open.map(o => <li key={o.value}><button type="button" disabled={o.disabled} onClick={() => onChange([...selected, o])}>
        Add {o.label}{o.note ? ` (${o.note})` : ""}</button></li>)}
      {!open.length && !failed && <li className="field-help">No matches.</li>}
    </ul>}
    <ul className="picker-list" aria-label={`Chosen ${noun}`}>
      {selected.map(o => <li key={o.value}>{o.label} <button type="button" onClick={() => onChange(selected.filter(s => s.value !== o.value))}>Remove {o.label}</button></li>)}
      {!selected.length && <li className="field-help">None chosen.</li>}
    </ul>
  </div>;
}
