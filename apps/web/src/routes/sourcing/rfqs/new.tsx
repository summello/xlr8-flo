import { Plus } from "@phosphor-icons/react";
import { useEffect, useRef, useState } from "react";
import { apiClient } from "../../../api/client";
import ErrorSummary, { type SummaryError } from "../../../components/form/ErrorSummary";
import Field from "../../../components/form/Field";
import Input from "../../../components/form/Input";
import Select from "../../../components/form/Select";
import LinePicker, { type SourceableLine } from "../../../components/sourcing/LinePicker";
import { failure } from "../../../components/requisitions/api";
import { writeHeaders } from "../../../features/auth/api";
import { formatQuantity, parseQuantity, scaled } from "../../../lib/decimal";
import "../../../styles/projects.css";
import "../../../styles/requisitions.css";
import "../../../styles/sourcing.css";

type Unit = { id: string; name: string };
type Units = { state: "loading" } | { state: "ok"; rows: Unit[] } | { state: "forbidden" } | { state: "error"; message: string };
type Submission = { fingerprint: string; key: string };

const ACCESS = "You cannot start RFQs here. Ask an administrator for the RFQ create permission.";
const IN_FLIGHT = "This RFQ is still being created. Wait a moment, then try again.";
const without = (record: Record<string, string>, key: string) => Object.fromEntries(Object.entries(record).filter(([k]) => k !== key));
const unitsPage = (signal: AbortSignal, cursor: string | null) => apiClient.GET("/api/v1/org/units", { signal, params: { query: { kind: "bu", active: true, page_size: 100, ...(cursor ? { cursor } : {}) } } });
const overOpen = (open: string, number: string) => `${formatQuantity(open)} are open on ${number}. Amend the requisition to source more`;

export default function NewRfq({ navigate }: { navigate: (href: string) => void }) {
  const params = new URLSearchParams(window.location.search);
  const requisition = params.get("requisition") ?? "";
  const unitParam = params.get("unit") ?? "";
  const unitName = params.get("unitName") ?? "The requisition's business unit";
  const [units, setUnits] = useState<Units>({ state: "loading" });
  const [unitsRetry, setUnitsRetry] = useState(0);
  const [unitId, setUnitId] = useState(unitParam);
  const [searchText, setSearchText] = useState(requisition);
  const [q, setQ] = useState(requisition);
  const [rows, setRows] = useState<SourceableLine[]>([]);
  const [next, setNext] = useState<string | null>(null);
  const [loaded, setLoaded] = useState("");
  const [moreLoading, setMoreLoading] = useState(false);
  const [linesError, setLinesError] = useState<string | null>(null);
  const [denied, setDenied] = useState(false);
  const [linesRetry, setLinesRetry] = useState(0);
  const [selected, setSelected] = useState<Record<string, string>>({});
  const [lineErrors, setLineErrors] = useState<Record<string, string>>({});
  const [title, setTitle] = useState("");
  const [titleError, setTitleError] = useState<string | undefined>();
  const [problems, setProblems] = useState<SummaryError[]>([]);
  const [busy, setBusy] = useState(false);
  const inFlight = useRef(false);
  const submission = useRef<Submission | null>(null);
  const summary = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const controller = new AbortController();
    void (async () => {
      const all: Unit[] = [];
      for (let cursor: string | null = null; ;) {
        const { data, error, response } = await unitsPage(controller.signal, cursor);
        if (!data) { setUnits(response?.status === 403 ? { state: "forbidden" } : { state: "error", message: failure(error, "Loading business units") }); return; }
        all.push(...data.rows.filter(u => u.active).map(u => ({ id: u.id, name: u.name })));
        if (!data.next_cursor) break;
        cursor = data.next_cursor;
      }
      setUnits({ state: "ok", rows: all });
    })().catch(() => { if (!controller.signal.aborted) setUnits({ state: "error", message: failure(undefined, "Loading business units") }); });
    return () => controller.abort();
  }, [unitsRetry]);

  const picking = units.state === "ok" || (units.state === "forbidden" && unitParam !== "");
  const key = `${unitId}|${q}|${linesRetry}`;
  const loading = picking && unitId !== "" && loaded !== key;
  const reset = () => { setRows([]); setNext(null); setSelected({}); setLineErrors({}); setProblems([]); setLinesError(null); setDenied(false); };
  const fetchPage = (signal: AbortSignal, cursor: string | null) => apiClient.GET("/api/v1/sourcing/requisition-lines", { signal, params: { query: { unit_id: unitId, limit: 100, ...(q ? { q } : {}), ...(cursor ? { cursor } : {}) } } });

  useEffect(() => {
    if (!picking || !unitId) return;
    const controller = new AbortController();
    void (async () => {
      const prefill = requisition !== "" && q === requisition;
      const all: SourceableLine[] = [];
      let cursor: string | null = null;
      do {
        const { data, error, response } = await fetchPage(controller.signal, cursor);
        if (!data) { if (response?.status === 403) setDenied(true); else setLinesError(failure(error, "Loading the lines")); return; }
        all.push(...data.rows);
        cursor = data.next_cursor;
      } while (prefill && cursor);
      setRows(all); setNext(cursor);
      if (prefill) {
        const chosen: Record<string, string> = {};
        let currency: string | null = null;
        for (const l of all) {
          if (!l.selectable || l.requisition_number !== requisition || (currency && l.currency !== currency)) continue;
          currency = l.currency; chosen[l.line_id] = formatQuantity(l.open_qty);
        }
        setSelected(chosen);
      }
    })().catch(() => { if (!controller.signal.aborted) setLinesError(failure(undefined, "Loading the lines")); }).finally(() => { if (!controller.signal.aborted) setLoaded(key); });
    return () => controller.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- fetchPage and requisition are fixed by unitId and q
  }, [picking, unitId, q, linesRetry]);

  useEffect(() => {
    if (!problems.length) return;
    const target = summary.current?.querySelector<HTMLElement>("[role=alert]");
    if (target) { target.tabIndex = -1; target.focus(); }
  }, [problems]);

  const firstId = Object.keys(selected)[0];
  const currency = rows.find(r => r.line_id === firstId)?.currency ?? null;

  const toggle = (line: SourceableLine, on: boolean) => {
    setSelected(s => on ? { ...s, [line.line_id]: formatQuantity(line.open_qty) } : without(s, line.line_id));
    if (!on) setLineErrors(e => without(e, line.line_id));
  };

  const search = () => { reset(); setQ(searchText.trim()); };

  const loadMore = async () => {
    if (!next) return;
    setMoreLoading(true);
    try {
      const { data, error, response } = await fetchPage(new AbortController().signal, next);
      if (data) { setRows(r => [...r, ...data.rows]); setNext(data.next_cursor); }
      else if (response?.status === 403) setDenied(true); else setLinesError(failure(error, "Loading more lines"));
    } catch { setLinesError(failure(undefined, "Loading more lines")); } finally { setMoreLoading(false); }
  };

  const fail = (errs: SummaryError[], lines: Record<string, string> = {}) => { setLineErrors(lines); setProblems(errs); };

  const create = async () => {
    if (inFlight.current) return;
    const lines: { requisition_line_id: string; requisition_version: number; quantity: string }[] = [];
    const errs: SummaryError[] = [];
    const byLine: Record<string, string> = {};
    const mark = (id: string, label: string, message: string) => { byLine[id] = message; errs.push({ fieldId: `qty-${id}`, label, message }); };
    const heading = title.trim() === "" ? "Enter a title for the RFQ." : undefined;
    setTitleError(heading);
    if (heading) errs.push({ fieldId: "rfq-title", label: "Title", message: heading });
    for (const l of rows) {
      if (!(l.line_id in selected)) continue;
      const label = `Line ${l.line_no} of ${l.requisition_number}`;
      const quantity = parseQuantity(selected[l.line_id]!);
      if (quantity === null) mark(l.line_id, label, `Enter a quantity like ${formatQuantity("1234.5")}.`);
      else if (scaled(quantity) > scaled(l.open_qty)) mark(l.line_id, label, overOpen(l.open_qty, l.requisition_number));
      else lines.push({ requisition_line_id: l.line_id, requisition_version: l.requisition_version, quantity });
    }
    if (!Object.keys(selected).length) errs.push({ fieldId: "rfq-lines", label: "Lines", message: "Choose at least one line to source." });
    if (errs.length) { fail(errs, byLine); return; }
    const body = { title: title.trim(), lines };
    const fingerprint = JSON.stringify(body);
    if (submission.current?.fingerprint !== fingerprint) submission.current = { fingerprint, key: crypto.randomUUID() };
    inFlight.current = true; setBusy(true); setProblems([]); setLineErrors({});
    try {
      const { data, error, response } = await apiClient.POST("/api/v1/sourcing/rfqs", { body, headers: { ...writeHeaders(), "Idempotency-Key": submission.current.key } });
      if (data) { submission.current = null; navigate(`/sourcing/rfqs/${data.id}`); return; }
      if (response.status === 403) fail([{ fieldId: "rfq-lines", label: "Access", message: ACCESS }]);
      else if (response.status === 409 && error?.type?.endsWith("/request_in_flight")) fail([{ fieldId: "rfq-title", label: "Create", message: IN_FLIGHT }]);
      else if (response.status === 409 && error?.checks?.problem === "over_open_quantity") {
        const fresh: Record<string, string> = {}, shown: Record<string, string> = {}, summaryRows: SummaryError[] = [];
        for (const l of rows) {
          const open = error.checks[`open.${l.line_id}`];
          if (open === undefined) continue;
          fresh[l.line_id] = open; shown[l.line_id] = overOpen(open, l.requisition_number);
          summaryRows.push({ fieldId: `qty-${l.line_id}`, label: `Line ${l.line_no} of ${l.requisition_number}`, message: shown[l.line_id]! });
        }
        setRows(r => r.map(l => l.line_id in fresh ? { ...l, open_qty: fresh[l.line_id]! } : l));
        fail(summaryRows, shown);
      } else fail([{ fieldId: "rfq-title", label: "Create", message: failure(error, "Creating the RFQ") }]);
    } catch {
      fail([{ fieldId: "rfq-title", label: "Create", message: failure(undefined, "Creating the RFQ") }]); // the key is kept: a retry replays the same RFQ
    } finally { inFlight.current = false; setBusy(false); }
  };

  const unitControl = units.state === "ok"
    ? <Field id="rfq-unit" label="Business Unit" help="One RFQ belongs to one business unit. Changing it clears your chosen lines.">
      <Select value={unitId} onChange={e => { reset(); setUnitId(e.target.value); }}><option value="">Choose a business unit</option>{units.rows.map(u => <option key={u.id} value={u.id}>{u.name}</option>)}</Select>
    </Field>
    : picking ? <p><strong>Business Unit</strong> <span>{unitName}</span></p> : undefined;

  return <section className="req-page" aria-label="New RFQ">
    <header className="req-heading"><p>Choose approved requisition lines and the quantity of each to source.</p></header>
    <div ref={summary}><ErrorSummary errors={problems} /></div>
    {units.state === "loading" && <div className="req-card req-card-skeleton" aria-busy="true"><p className="req-skeleton" role="status" aria-label="Loading business units" /></div>}
    {units.state === "error" && <div role="alert"><p>{units.message}</p><button type="button" onClick={() => { setUnits({ state: "loading" }); setUnitsRetry(n => n + 1); }}>Try again</button></div>}
    {units.state === "forbidden" && unitParam === "" && <section className="req-card" aria-label="Start from a requisition"><p>Start an RFQ from an approved requisition. Open it and choose Start RFQ.</p><a className="req-link-button" href="/requisitions" onClick={e => { e.preventDefault(); navigate("/requisitions"); }}>Go to Requisitions</a></section>}
    {picking && <form className="req-card" noValidate onSubmit={e => { e.preventDefault(); void create(); }}>
      {unitControl}
      <Field id="rfq-title" label="Title" required help="What you are asking vendors to quote." error={titleError}>
        <Input autoComplete="off" inputMode="text" value={title} onChange={e => setTitle(e.target.value)} />
      </Field>
      <h2 id="rfq-lines" tabIndex={-1}>Approved Lines</h2>
      {unitId && <div className="rfq-filters"><Field id="rfq-search" label="Search Lines" help="Requisition number or description.">
        <Input autoComplete="off" inputMode="search" value={searchText} onChange={e => setSearchText(e.target.value)} onKeyDown={e => { if (e.key === "Enter") { e.preventDefault(); search(); } }} />
      </Field><button type="button" onClick={() => search()}>Search</button></div>}
      {!unitId && <p className="req-sub">Choose a business unit to see its approved lines.</p>}
      {denied && <p role="alert">{ACCESS}</p>}
      {linesError && <div role="alert"><p>{linesError}</p><button type="button" onClick={() => { reset(); setLinesRetry(n => n + 1); }}>Try again</button></div>}
      {loading && rows.length === 0 && <p className="req-skeleton" role="status" aria-label="Loading lines" aria-busy="true" />}
      {unitId && !loading && !denied && !linesError && rows.length === 0 && <p>No approved lines are open to source here.</p>}
      {rows.length > 0 && <LinePicker rows={rows} selected={selected} errors={lineErrors} currency={currency} onToggle={toggle} onQuantity={(id, text) => setSelected(s => ({ ...s, [id]: text }))} />}
      {next && <button type="button" aria-busy={moreLoading || undefined} onClick={() => void loadMore()}>Load More Lines</button>}
      <div className="req-actions"><button type="submit" className="form-submit" aria-busy={busy || undefined}><Plus aria-hidden="true" weight="regular" />{busy ? "Creating RFQ Draft" : "Create RFQ Draft"}</button></div>
    </form>}
  </section>;
}
