import { Minus, Plus } from "@phosphor-icons/react";
import type { Band, Choice, Points, QuestionType } from "./api";

type Props = { type: QuestionType; value: Points; onChange: (next: Points) => void; label: string };

const num = (value: string, onChange: (v: string) => void, label: string) => (
  <input type="number" inputMode="numeric" min={0} max={100} aria-label={label} value={value} onChange={e => onChange(e.target.value)} />
);

/** Points per answer: yes/no, each choice, or each number band. Other types score nothing by answer. */
export default function PointsEditor({ type, value, onChange, label }: Props) {
  if (type === "yes_no") {
    return (
      <fieldset className="vq-points">
        <legend>Points per answer</legend>
        <label>Yes {num(value.yes, yes => onChange({ ...value, yes }), `${label}: points for yes`)}</label>
        <label>No {num(value.no, no => onChange({ ...value, no }), `${label}: points for no`)}</label>
      </fieldset>
    );
  }
  if (type === "single_choice" || type === "multi_choice") {
    const set = (i: number, patch: Partial<Choice>) => onChange({ ...value, choices: value.choices.map((c, j) => (j === i ? { ...c, ...patch } : c)) });
    return (
      <fieldset className="vq-points">
        <legend>Choices and points</legend>
        {value.choices.map((c, i) => (
          <div className="vq-pair" key={c.value ?? `new-${i}`}>
            <input aria-label={`${label}: choice ${i + 1} label`} value={c.label} maxLength={200} onChange={e => set(i, { label: e.target.value })} />
            {num(c.points, points => set(i, { points }), `${label}: choice ${i + 1} points`)}
            <button type="button" aria-label={`${label}: remove choice ${i + 1}`} disabled={value.choices.length === 1} onClick={() => onChange({ ...value, choices: value.choices.filter((_, j) => j !== i) })}><Minus aria-hidden="true" />Remove</button>
          </div>
        ))}
        <button type="button" onClick={() => onChange({ ...value, choices: [...value.choices, { label: "", points: "0" }] })}><Plus aria-hidden="true" />Add Choice</button>
      </fieldset>
    );
  }
  if (type === "number") {
    const set = (i: number, patch: Partial<Band>) => onChange({ ...value, bands: value.bands.map((b, j) => (j === i ? { ...b, ...patch } : b)) });
    return (
      <fieldset className="vq-points">
        <legend>Number bands (blank limit is open)</legend>
        {value.bands.map((b, i) => (
          <div className="vq-pair" key={i}>
            <input type="number" aria-label={`${label}: band ${i + 1} from`} value={b.min} onChange={e => set(i, { min: e.target.value })} />
            <input type="number" aria-label={`${label}: band ${i + 1} to`} value={b.max} onChange={e => set(i, { max: e.target.value })} />
            {num(b.points, points => set(i, { points }), `${label}: band ${i + 1} points`)}
            <button type="button" aria-label={`${label}: remove band ${i + 1}`} onClick={() => onChange({ ...value, bands: value.bands.filter((_, j) => j !== i) })}><Minus aria-hidden="true" />Remove</button>
          </div>
        ))}
        <button type="button" onClick={() => onChange({ ...value, bands: [...value.bands, { min: "", max: "", points: "0" }] })}><Plus aria-hidden="true" />Add Band</button>
      </fieldset>
    );
  }
  return null;
}
