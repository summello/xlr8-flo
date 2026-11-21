import { ArrowDown, ArrowUp, Circle, ArrowsClockwise, Archive, Plus } from "@phosphor-icons/react";
import { useCallback, useEffect, useRef, useState } from "react";
import { apiClient } from "../../../api/client";
import { failure, formatDate, type Problem } from "../../../components/requisitions/api";
import { useLoad } from "../../../components/requisitions/useLoad";
import { mutationHeaders, patchHeaders, pointsSummary, TYPE_LABELS, type Question, type VersionDetail, type VersionRead } from "../../../components/vendors/questionnaire/api";
import Preview from "../../../components/vendors/questionnaire/Preview";
import PublishDialog from "../../../components/vendors/questionnaire/PublishDialog";
import QuestionForm, { type QuestionDraft } from "../../../components/vendors/questionnaire/QuestionForm";
import Signal from "../../../components/vendors/questionnaire/Signal";
import "../../../components/vendors/questionnaire/questionnaire.css";

const BASE = "/api/v1/admin/vendor-questionnaires" as const;
const STATUS = { draft: ["Draft", Circle], published: ["Published", ArrowsClockwise], retired: ["Retired", Archive] } as const;

function Pill({ status }: { status: VersionRead["status"] }) {
  const [label, Icon] = STATUS[status];
  return <span className="vq-pill" data-status={status}><Icon aria-hidden="true" weight="regular" />{label}</span>;
}

export default function VendorQuestionnaires() {
  const [selected, setSelected] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const noticeRef = useRef<HTMLParagraphElement>(null);
  const summaryRef = useRef<HTMLDivElement>(null);
  const list = useLoad<VersionRead[]>(async () => {
    const r = await apiClient.GET(BASE);
    if (!r.data) throw new Error("Versions could not load. Check your connection and retry.");
    return r.data;
  }, []);
  const detail = useLoad<VersionDetail | null>(async () => {
    if (!selected) return null;
    const r = await apiClient.GET(`${BASE}/{vid}`, { params: { path: { vid: selected } } });
    if (!r.data) throw new Error("This version could not load. Check your connection and retry.");
    return r.data;
  }, [selected]);
  const data = detail.data;

  const refresh = useCallback(async () => {
    if (!selected) return;
    const r = await apiClient.GET(`${BASE}/{vid}`, { params: { path: { vid: selected } } });
    if (r.data) detail.set(r.data);
    list.reload();
  }, [selected, detail, list]);

  const say = (message: string) => { setNotice(message); setProblem(null); setTimeout(() => noticeRef.current?.focus()); };
  const fail = (error: unknown, action: string) => { setProblem(failure(error as Problem | undefined, action)); setNotice(null); setTimeout(() => summaryRef.current?.focus()); };

  async function create() {
    const r = await apiClient.POST(BASE, { headers: mutationHeaders(crypto.randomUUID()) });
    if (!r.data) return fail(r.error, "Creating the draft");
    setSelected(r.data.id); list.reload(); say(`Draft ${r.data.number} created.`);
  }
  async function copy(v: VersionDetail) {
    const r = await apiClient.POST(`${BASE}/{vid}/copy`, { params: { path: { vid: v.id } }, headers: mutationHeaders(crypto.randomUUID()) });
    if (!r.data) return fail(r.error, "Copying the version");
    setSelected(r.data.id); list.reload(); say(`Draft ${r.data.number} created from version ${v.number}.`);
  }

  return (
    <section className="vq" aria-label="Vendor Questionnaires">
      <div className="vq-head">
        <h2>Vendor Questionnaires</h2>
        <button type="button" onClick={() => void create()}><Plus aria-hidden="true" />New Draft</button>
      </div>
      <p>The questions a requester answers when asking for a new vendor. A published version never changes.</p>
      {notice ? <p role="status" tabIndex={-1} ref={noticeRef}>{notice}</p> : null}
      {problem ? <div className="vq-error" role="alert" tabIndex={-1} ref={summaryRef}>{problem}</div> : null}

      <div className="vq-card">
        <h3>Versions</h3>
        {list.loading && !list.data ? <p role="status">Loading versions…</p>
          : list.error && !list.data ? <p role="alert">{list.error} <button type="button" onClick={list.reload}>Retry</button></p>
          : list.data?.length === 0 ? <p>No questionnaire yet. Choose New Draft to write the first one.</p>
          : (
            <table>
              <caption>Questionnaire versions</caption>
              <thead><tr><th scope="col">Version</th><th scope="col">Status</th><th scope="col">Questions</th><th scope="col">Weights</th><th scope="col">Published</th></tr></thead>
              <tbody>{list.data?.map(v => (
                <tr key={v.id}>
                  <th scope="row"><button type="button" aria-current={v.id === selected ? "true" : undefined} onClick={() => { setSelected(v.id); setProblem(null); }}>Open Version {v.number}</button></th>
                  <td><Pill status={v.status} /></td>
                  <td className="num">{v.question_count}</td>
                  <td className="num">{v.weight_total}</td>
                  <td>{v.published_at ? <time dateTime={v.published_at}>{formatDate(v.published_at)}</time> : "—"}</td>
                </tr>
              ))}</tbody>
            </table>
          )}
      </div>

      {selected ? (
        detail.loading && !data ? <p role="status">Loading version…</p>
        : detail.error && !data ? <p role="alert">{detail.error} <button type="button" onClick={detail.reload}>Retry</button></p>
        : data ? <Editor key={data.id} v={data} detail={detail} refresh={refresh} say={say} fail={fail} copy={copy} onSummary={setProblem} summaryRef={summaryRef} />
        : null
      ) : null}
    </section>
  );
}

type EditorProps = {
  v: VersionDetail;
  detail: { set: (v: VersionDetail | null) => void };
  refresh: () => Promise<void>;
  say: (m: string) => void;
  fail: (e: unknown, a: string) => void;
  copy: (v: VersionDetail) => Promise<void>;
  onSummary: (m: string | null) => void;
  summaryRef: React.RefObject<HTMLDivElement | null>;
};

function Editor({ v, detail, refresh, say, fail, copy, onSummary, summaryRef }: EditorProps) {
  const draft = v.status === "draft";
  const [threshold, setThreshold] = useState(String(v.threshold));
  const [publishing, setPublishing] = useState(false);
  const [busy, setBusy] = useState(false);
  const publishKey = useRef(crypto.randomUUID());
  const taken = new Set(v.questions.map(q => q.key));
  // A reorder moves the row's DOM node, which drops focus; put it back on the button that was pressed.
  const focusAfter = useRef<string | null>(null);
  useEffect(() => {
    if (focusAfter.current) { document.getElementById(focusAfter.current)?.focus(); focusAfter.current = null; }
  }, [v]);

  async function saveThreshold() {
    const r = await apiClient.PATCH(`${BASE}/{vid}`, { params: { path: { vid: v.id } }, headers: patchHeaders(v.row_version), body: { threshold: Number(threshold) } });
    if (!r.data) { fail(r.error, "Saving the threshold"); await refresh(); return; }
    detail.set(r.data); say("Threshold saved.");
  }
  async function add(q: QuestionDraft): Promise<string | null> {
    const r = await apiClient.POST(`${BASE}/{vid}/questions`, { params: { path: { vid: v.id } }, headers: mutationHeaders(crypto.randomUUID()), body: { ...q, options: q.options as never } });
    if (!r.data) return failure(r.error as Problem | undefined, "Adding the question");
    await refresh(); return null;
  }
  async function save(q: Question, d: QuestionDraft): Promise<string | null> {
    const r = await apiClient.PATCH(`${BASE}/{vid}/questions/{key}`, { params: { path: { vid: v.id, key: q.key } }, headers: patchHeaders(q.row_version), body: { prompt: d.prompt, required: d.required, weight: d.weight, options: d.options as never } });
    if (!r.data) {
      const message = failure(r.error as Problem | undefined, "Saving the question");
      if (r.response.status === 409) await refresh();
      return message;
    }
    await refresh(); return null;
  }
  async function move(q: Question, direction: "up" | "down") {
    focusAfter.current = `move-${q.key}-${direction}`;
    const r = await apiClient.POST(`${BASE}/{vid}/questions/{key}/move`, { params: { path: { vid: v.id, key: q.key } }, headers: mutationHeaders(crypto.randomUUID()), body: { direction } });
    if (!r.data) { focusAfter.current = null; fail(r.error, "Moving the question"); return; }
    detail.set(r.data);
  }
  async function publish() {
    setBusy(true);
    const r = await apiClient.POST(`${BASE}/{vid}/publish`, { params: { path: { vid: v.id } }, headers: mutationHeaders(publishKey.current) });
    setBusy(false); setPublishing(false);
    if (!r.data) {
      const error = r.error as Problem | undefined;
      await refresh();
      if (error?.checks?.problem === "weights_total") {
        onSummary(`${error.detail ?? "The weights must add up to exactly 100."} Weights total ${error.checks.total} of 100. Your draft is kept; change the weights and publish again.`);
        setTimeout(() => summaryRef.current?.focus());
      } else fail(error, "Publishing");
      return;
    }
    publishKey.current = crypto.randomUUID();
    detail.set(r.data); say(`Version ${r.data.number} is published and read-only.`);
  }

  return (
    <div className="vq-card">
      <div className="vq-head">
        <h3>Version {v.number}</h3>
        <Pill status={v.status} />
        {v.published_at ? <span>Published <time dateTime={v.published_at}>{formatDate(v.published_at)}</time></span> : null}
      </div>
      <Signal total={v.weight_total} />
      {draft ? (
        <form className="vq-actions" noValidate onSubmit={e => { e.preventDefault(); void saveThreshold(); }}>
          <label htmlFor="vq-threshold">Threshold (0 to 100)</label>
          <input id="vq-threshold" type="number" inputMode="numeric" min={0} max={100} value={threshold} onChange={e => setThreshold(e.target.value)} />
          <button type="submit">Save Threshold</button>
        </form>
      ) : <p>Threshold {v.threshold} of 100. This version is read-only.</p>}

      {draft ? (
        <>
          <p>Weights and points are saved with each question. The total comes from the server after every save.</p>
          {v.questions.length === 0 ? <p>No questions yet. Add the first one below.</p> : null}
          <ol className="vq-list" aria-label="Questions">
            {v.questions.map((q, i) => (
              <li key={q.key}>
                <QuestionForm question={q} takenKeys={taken} submitLabel="Save Question" onSubmit={d => save(q, d)}>
                  <button type="button" id={`move-${q.key}-up`} aria-label={`Move "${q.prompt}" up`} aria-disabled={i === 0} onClick={() => { if (i > 0) void move(q, "up"); }}><ArrowUp aria-hidden="true" />Up</button>
                  <button type="button" id={`move-${q.key}-down`} aria-label={`Move "${q.prompt}" down`} aria-disabled={i === v.questions.length - 1} onClick={() => { if (i < v.questions.length - 1) void move(q, "down"); }}><ArrowDown aria-hidden="true" />Down</button>
                </QuestionForm>
              </li>
            ))}
          </ol>
          <h3>Add a Question</h3>
          <QuestionForm takenKeys={taken} submitLabel="Add Question" onSubmit={add} />
          <div className="vq-actions"><button type="button" onClick={() => setPublishing(true)}>Publish</button></div>
        </>
      ) : (
        <>
          <table>
            <caption>Questions in version {v.number}</caption>
            <thead><tr><th scope="col">Prompt</th><th scope="col">Type</th><th scope="col">Required</th><th scope="col">Weight</th><th scope="col">Points</th></tr></thead>
            <tbody>{v.questions.map(q => (
              <tr key={q.key}><th scope="row">{q.prompt}</th><td>{TYPE_LABELS[q.type]}</td><td>{q.required ? "Yes" : "No"}</td><td className="num">{q.weight}</td><td>{pointsSummary(q)}</td></tr>
            ))}</tbody>
          </table>
          <div className="vq-actions"><button type="button" onClick={() => void copy(v)}>Copy to New Draft</button></div>
        </>
      )}
      <Preview questions={v.questions} />
      {publishing ? <PublishDialog number={v.number} busy={busy} onConfirm={() => void publish()} onClose={() => setPublishing(false)} /> : null}
    </div>
  );
}
