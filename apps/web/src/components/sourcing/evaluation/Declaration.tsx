import { useRef, useState } from "react";
import { apiClient } from "../../../api/client";
import { failure, mutationHeaders, type Problem } from "../../requisitions/api";

/** SRC-011: the first thing an evaluator sees. `known` is the later "I have a conflict" action open to someone who declared none. */
export default function Declaration({ id, known, onDone }: { id: string; known?: boolean; onDone: () => void }) {
  const [choice, setChoice] = useState<"yes" | "no" | null>(known ? "yes" : null);
  const [detail, setDetail] = useState("");
  const [error, setError] = useState<{ field: "choice" | "detail"; text: string } | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const key = useRef<string | null>(null);
  const choiceRef = useRef<HTMLInputElement>(null), detailRef = useRef<HTMLTextAreaElement>(null);
  const refuse = (field: "choice" | "detail", text: string) => { setError({ field, text }); (field === "choice" ? choiceRef : detailRef).current?.focus(); };

  const submit = async () => {
    setMessage(null);
    if (!choice) return refuse("choice", "Answer yes or no.");
    if (choice === "yes" && !detail.trim()) return refuse("detail", "Describe the conflict.");
    setError(null);
    key.current ??= crypto.randomUUID(); // one key per attempt, reused on retry
    setBusy(true);
    try {
      const { data, error: problem, response } = await apiClient.POST("/api/v1/sourcing/rfqs/{id}/evaluation/declaration", { params: { path: { id } }, headers: mutationHeaders(key.current), body: { conflict: choice === "yes", ...(choice === "yes" ? { detail: detail.trim() } : {}) } });
      if (data || response.status === 409) { key.current = null; onDone(); return; } // 409: already declared; the page reloads what the server holds
      const p = problem as Problem | undefined;
      if (response.status === 422) { key.current = null; refuse("detail", p?.errors?.[0]?.message ?? p?.detail ?? "That declaration was refused."); }
      else setMessage(failure(p, "Saving your declaration"));
    } catch {
      setMessage(failure(undefined, "Saving your declaration"));
    } finally { setBusy(false); }
  };

  const bad = (field: string) => error?.field === field || undefined;
  return <form className="req-card eval-declaration" aria-labelledby={`eval-declare-${known ? "later" : "first"}`} noValidate onSubmit={e => { e.preventDefault(); void submit(); }}>
    <h2 id={`eval-declare-${known ? "later" : "first"}`}>{known ? "Declare a Conflict" : "Conflict of Interest"}</h2>
    {!known && <fieldset className="req-field" data-invalid={bad("choice")} aria-describedby={error?.field === "choice" ? "eval-choice-error" : undefined}>
      <legend className="t-label">Do you have a conflict of interest with any invited vendor?</legend>
      <label className="eval-radio"><input ref={choiceRef} type="radio" name="conflict" value="no" checked={choice === "no"} onChange={() => setChoice("no")} aria-invalid={bad("choice")} />No</label>
      <label className="eval-radio"><input type="radio" name="conflict" value="yes" checked={choice === "yes"} onChange={() => setChoice("yes")} />Yes</label>
      {error?.field === "choice" && <p role="alert" id="eval-choice-error" className="field-error">{error.text}</p>}
    </fieldset>}
    {choice === "yes" && <div className="req-field" data-invalid={bad("detail")}>
      <label className="t-label" htmlFor={`eval-detail-${known ? "later" : "first"}`}>Describe the conflict (required)</label>
      <textarea id={`eval-detail-${known ? "later" : "first"}`} ref={detailRef} rows={3} maxLength={2000} value={detail} onChange={e => setDetail(e.target.value)} required aria-invalid={bad("detail")} aria-describedby={error?.field === "detail" ? "eval-detail-error" : undefined} />
      {error?.field === "detail" && <p role="alert" id="eval-detail-error" className="field-error">{error.text}</p>}
    </div>}
    {message && <p role="alert" className="field-error">{message} Your entries are kept.</p>}
    <div className="req-actions"><button type="submit" aria-busy={busy || undefined} disabled={busy}>{busy ? "Saving…" : known ? "Declare Conflict" : "Save Declaration"}</button></div>
  </form>;
}
