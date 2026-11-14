import { useId } from "react";
import type { Question } from "./api";
import { pointsFrom } from "./api";

function Control({ q, labelId }: { q: Question; labelId: string }) {
  const id = useId();
  const choices = pointsFrom(q.type, q.options).choices;
  const name = `${id}-${q.key}`;
  if (q.type === "yes_no") return <>{["Yes", "No"].map(v => <label key={v}><input type="radio" name={name} />{v}</label>)}</>;
  if (q.type === "single_choice") return <>{choices.map(c => <label key={c.value}><input type="radio" name={name} />{c.label}</label>)}</>;
  if (q.type === "multi_choice") return <>{choices.map(c => <label key={c.value}><input type="checkbox" />{c.label}</label>)}</>;
  const kind = q.type === "number" ? "number" : q.type === "date" ? "date" : q.type === "file" ? "file" : "text";
  return <input id={name} type={kind} aria-labelledby={labelId} />;
}

function PreviewQuestion({ q }: { q: Question }) {
  const labelId = `${useId()}-label`;
  return (
    <div className="vq-preview-q">
      <p id={labelId}>{q.prompt}{q.required ? <span> (required)</span> : null}</p>
      <Control q={q} labelId={labelId} />
    </div>
  );
}

/** What the requester sees: prompts, types and required marks. Never a weight or points (vendors.md 3.3). */
export default function Preview({ questions }: { questions: readonly Question[] }) {
  return (
    <details className="vq-preview">
      <summary>Preview of the Request Form</summary>
      {questions.length === 0 ? <p>Add a question to see the form.</p> : (
        <fieldset disabled>
          <legend>Preview only. Nothing here is saved.</legend>
          {questions.map(q => (
            <PreviewQuestion key={q.key} q={q} />
          ))}
        </fieldset>
      )}
    </details>
  );
}
