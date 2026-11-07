import { useId, useRef, useState } from "react";
import PointsEditor from "./PointsEditor";
import { TYPES, TYPE_LABELS, emptyPoints, optionsFor, pointsFrom, uniqueKey, type Question, type QuestionType } from "./api";

export type QuestionDraft = { key: string; prompt: string; type: QuestionType; required: boolean; weight: number; options: unknown };

type Props = {
  question?: Question;
  takenKeys: ReadonlySet<string>;
  submitLabel: string;
  /** Resolves to an error message, or null when saved. */
  onSubmit: (draft: QuestionDraft) => Promise<string | null>;
  /** Extra controls placed beside the submit button (the move buttons). */
  children?: React.ReactNode;
};

/** One question's fields. The same form adds a question and edits one; an existing question's type is fixed (E11-S05). */
export default function QuestionForm({ question, takenKeys, submitLabel, onSubmit, children }: Props) {
  const id = useId();
  const [prompt, setPrompt] = useState(question?.prompt ?? "");
  const [type, setType] = useState<QuestionType>(question?.type ?? "text");
  const [required, setRequired] = useState(question?.required ?? false);
  const [weight, setWeight] = useState(String(question?.weight ?? 0));
  const [points, setPoints] = useState(() => (question ? pointsFrom(question.type, question.options) : emptyPoints("text")));
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const promptRef = useRef<HTMLInputElement>(null);
  const alertRef = useRef<HTMLParagraphElement>(null);
  const label = prompt.trim() || "New question";

  async function submit() {
    setBusy(true); setError(null); setSaved(false);
    const result = await onSubmit({
      key: question?.key ?? uniqueKey(prompt, takenKeys, "question"),
      prompt: prompt.trim(), type, required, weight: Number(weight), options: optionsFor(type, points),
    });
    setBusy(false);
    if (result) { setError(result); setTimeout(() => alertRef.current?.focus()); return; }
    if (question) setSaved(true);
    else { setPrompt(""); setRequired(false); setWeight("0"); setPoints(emptyPoints(type)); promptRef.current?.focus(); }
  }

  return (
    <form className="vq-form" noValidate onSubmit={e => { e.preventDefault(); void submit(); }}>
      {error ? <p className="vq-error" role="alert" tabIndex={-1} ref={alertRef}>{error}</p> : null}
      <div className="vq-grid">
        <label htmlFor={`${id}-prompt`}>Prompt</label>
        <input id={`${id}-prompt`} ref={promptRef} value={prompt} maxLength={500} required aria-required="true" onChange={e => setPrompt(e.target.value)} />
        <label htmlFor={`${id}-type`}>Type</label>
        {question
          ? <p id={`${id}-type`}>{TYPE_LABELS[type]}</p>
          : <select id={`${id}-type`} value={type} onChange={e => { const next = e.target.value as QuestionType; setType(next); setPoints(emptyPoints(next)); }}>{TYPES.map(t => <option key={t} value={t}>{TYPE_LABELS[t]}</option>)}</select>}
        <label htmlFor={`${id}-weight`}>Weight</label>
        <input id={`${id}-weight`} type="number" inputMode="numeric" min={0} max={100} value={weight} onChange={e => setWeight(e.target.value)} />
        <span />
        <label className="vq-check"><input type="checkbox" checked={required} onChange={e => setRequired(e.target.checked)} />Required</label>
      </div>
      <PointsEditor type={type} value={points} onChange={setPoints} label={label} />
      <div className="vq-actions">
        <button type="submit" aria-busy={busy}>{submitLabel}</button>
        {children}
        {saved ? <span role="status">Saved.</span> : null}
      </div>
    </form>
  );
}
