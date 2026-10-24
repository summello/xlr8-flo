import { ArrowDown, ArrowUp, CalendarX, Info, MagnifyingGlass, Warning } from "@phosphor-icons/react";
import { useCallback, useEffect, useRef, useState, type KeyboardEvent } from "react";

import { apiClient } from "../../api/client";
import type { components, operations } from "../../api/generated/schema";
import DecisionPanel from "../../components/approvals/DecisionPanel";
import InboxStatus from "../../components/approvals/InboxStatus";
import {
  EMPTY_COPY,
  TABS,
  TAB_LABELS,
  overdueLabel,
  parseTab,
  stepLabel,
  subline,
  type InboxPage,
  type InboxRow,
  type Tab,
} from "../../components/approvals/inbox-model";
import Money from "../../components/ui/Money";
import Person from "../../components/ui/Person";
import "../../styles/approvals.css";

type Sort = "due" | "amount" | "decided";
type Direction = "asc" | "desc";
type DocumentType = NonNullable<operations["inbox_route_api_v1_approvals_tasks_get"]["parameters"]["query"]>["type"] & string;
const TYPE_OPTIONS: readonly [string, string][] = [
  ["requisition", "Requisition"],
  ["project", "Project"],
  ["budget_change", "Budget change"],
  ["vendor", "Vendor"],
  ["rfq", "RFQ"],
  ["award", "Award"],
  ["po", "Purchase order"],
];
const TYPE_LABELS = Object.fromEntries(TYPE_OPTIONS);

type Failure = { message: string; reference: string | null };

export default function ApprovalInbox({ navigate }: { navigate: (href: string) => void }) {
  const [tab, setTab] = useState<Tab>(() => parseTab(window.location.search));
  const [search, setSearch] = useState("");
  const [q, setQ] = useState("");
  const [type, setType] = useState("");
  const [sort, setSort] = useState<{ key: Sort; direction: Direction } | null>(null);
  const [cursor, setCursor] = useState<string>();
  const [previous, setPrevious] = useState<(string | undefined)[]>([]);
  const [page, setPage] = useState<InboxPage>();
  const [loading, setLoading] = useState(true);
  const [failure, setFailure] = useState<Failure>();
  const [partial, setPartial] = useState(false);
  const [reload, setReload] = useState(0);
  const [open, setOpen] = useState<InboxRow>();
  const [now] = useState(() => Date.now());
  const [away, setAway] = useState<components["schemas"]["DelegationRead"]>();
  const tabRefs = useRef<Record<Tab, HTMLButtonElement | null>>({ pending: null, overdue: null, completed: null, delegated: null });
  const opener = useRef<HTMLElement | null>(null);
  const loaded = useRef(false);

  useEffect(() => {
    let active = true;
    void Promise.resolve().then(() => { if (active) { setLoading(true); setFailure(undefined); } });
    apiClient
      .GET("/api/v1/approvals/tasks", {
        params: {
          query: {
            view: tab,
            type: (type || undefined) as DocumentType | undefined,
            q: q || undefined,
            sort: sort?.key,
            direction: sort?.direction,
            cursor,
          },
        },
      })
      .then((response) => {
        if (!active) return;
        if (response.data) {
          loaded.current = true;
          setPage(response.data);
          setPartial(false);
        } else if (loaded.current && cursor !== undefined) {
          // A later page failed: keep what is on screen and say so.
          setPartial(true);
        } else {
          setFailure({
            message: "Could not load your approvals. Your search and filters are kept.",
            reference: response.error?.correlation_id ?? null,
          });
        }
        setLoading(false);
      })
      .catch(() => {
        if (!active) return;
        setFailure({ message: "Could not load your approvals. Your search and filters are kept.", reference: null });
        setLoading(false);
      });
    return () => { active = false; };
  }, [tab, type, q, sort, cursor, reload]);

  useEffect(() => {
    let active = true;
    void apiClient.GET("/api/v1/approvals/delegations", { params: { query: { view: "mine" } } })
      .then((r) => { if (active) setAway(r.data?.rows.find((d) => d.state === "active" || d.state === "scheduled")); })
      .catch(() => undefined);
    return () => { active = false; };
  }, []);

  const restart = useCallback(() => { setCursor(undefined); setPrevious([]); }, []);
  const choose = (next: Tab, focus = false) => {
    setTab(next);
    setSort(null);
    restart();
    window.history.replaceState(null, "", `/approvals?tab=${next}`);
    if (focus) tabRefs.current[next]?.focus();
  };
  const onTabKey = (event: KeyboardEvent<HTMLButtonElement>) => {
    const index = TABS.indexOf(tab);
    const target = { ArrowRight: TABS[(index + 1) % TABS.length], ArrowLeft: TABS[(index + TABS.length - 1) % TABS.length], Home: TABS[0], End: TABS[TABS.length - 1] }[event.key];
    if (target === undefined) return;
    event.preventDefault();
    choose(target, true);
  };
  const sortBy = (key: Sort) => {
    restart();
    setSort((current) => current?.key === key && current.direction === "asc" ? { key, direction: "desc" } : { key, direction: "asc" });
  };
  const ariaSort = (key: Sort) => sort?.key === key ? (sort.direction === "asc" ? "ascending" : "descending") : "none";
  const refreshRow = useCallback(async (row: InboxRow) => {
    const response = await apiClient.GET("/api/v1/approvals/tasks", { params: { query: { view: tab, q: row.number } } });
    return response.data?.rows.find((candidate) => candidate.task_id === row.task_id);
  }, [tab]);
  const filtered = q !== "" || type !== "";
  const rows = page?.rows ?? [];
  const dateHeading: Sort = tab === "completed" ? "decided" : "due";

  return (
    <section className="appr" aria-label="Approval inbox">
      <header className="appr-head">
        <div>
          <p className="appr-sub">{page ? subline(page.counts, page.total_amount) : "Loading your approvals"}</p>
        </div>
        <a className="appr-button" href="/approvals/availability" onClick={(event) => { event.preventDefault(); navigate("/approvals/availability"); }}>Set Out-of-Office</a>
      </header>
      {away && (
        <p role="status" className="appr-note">
          <Info aria-hidden="true" />
          {away.state === "active" ? "You are out of office." : "Your out-of-office is scheduled."} Approvals go to <Person name={away.delegate_name} email={away.delegate_email} /> until <time dateTime={away.ends_at}>{formatDate(away.ends_at)}</time>.{" "}
          <a href="/approvals/availability" onClick={(event) => { event.preventDefault(); navigate("/approvals/availability"); }}>Change it</a>
        </p>
      )}
      <div className="appr-card" aria-busy={loading}>
        <h2 className="appr-card-title">Approval Queue</h2>
        <div role="tablist" aria-label="Approval views" className="appr-tabs">
          {TABS.map((name) => (
            <button
              key={name}
              id={`appr-tab-${name}`}
              ref={(node) => { tabRefs.current[name] = node; }}
              role="tab"
              type="button"
              aria-selected={tab === name}
              aria-controls="appr-panel"
              tabIndex={tab === name ? 0 : -1}
              onClick={() => choose(name)}
              onKeyDown={onTabKey}
            >
              {TAB_LABELS[name]}
              <span className="appr-count">{page ? page.counts[name] : "–"}</span>
            </button>
          ))}
        </div>
        <div id="appr-panel" role="tabpanel" tabIndex={-1} aria-labelledby={`appr-tab-${tab}`}>
          <form className="appr-toolbar" role="search" onSubmit={(event) => { event.preventDefault(); restart(); setQ(search.trim()); }}>
            <label htmlFor="appr-search">Search</label>
            <input id="appr-search" type="search" autoComplete="off" placeholder="Number, title or requester" value={search} onChange={(event) => setSearch(event.target.value)} />
            <label htmlFor="appr-type">Type</label>
            <select id="appr-type" value={type} onChange={(event) => { restart(); setType(event.target.value); }}>
              <option value="">All types</option>
              {TYPE_OPTIONS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
            </select>
            <button type="submit"><MagnifyingGlass aria-hidden="true" />Search</button>
            {filtered && <button type="button" onClick={() => { setSearch(""); setQ(""); setType(""); restart(); }}>Clear filters</button>}
          </form>
          {partial && <p role="status" className="appr-note"><Warning aria-hidden="true" />Some approvals did not load. <button type="button" onClick={() => setReload((n) => n + 1)}>Try again</button></p>}
          {failure ? (
            <div role="alert" className="appr-state">
              <h3>Could not load your approvals</h3>
              <p>{failure.message}{failure.reference && <> Reference <code>{failure.reference}</code>.</>}</p>
              <button type="button" onClick={() => setReload((n) => n + 1)}>Try again</button>
            </div>
          ) : loading && !page ? (
            <div role="status" aria-label="Loading approvals" className="appr-skeleton">
              {Array.from({ length: 5 }, (_, index) => <div key={index} className="appr-skeleton-row" />)}
            </div>
          ) : rows.length === 0 ? (
            <div className="appr-state">
              <h3>{filtered ? "No Approvals Match" : EMPTY_COPY[tab].title}</h3>
              <p>{filtered ? "Change the search or filters to see more." : EMPTY_COPY[tab].body}</p>
              {filtered && <button type="button" onClick={() => { setSearch(""); setQ(""); setType(""); restart(); }}>Clear filters</button>}
            </div>
          ) : (
            <>
              <table className="appr-table">
                <caption className="visually-hidden">{TAB_LABELS[tab]} approvals</caption>
                <thead>
                  <tr>
                    <th scope="col">Document</th>
                    <th scope="col" className="appr-opt">Type</th>
                    <th scope="col" className="appr-num" aria-sort={ariaSort("amount")}>
                      <button type="button" onClick={() => sortBy("amount")}>Amount{sort?.key === "amount" && (sort.direction === "asc" ? <ArrowUp aria-hidden="true" /> : <ArrowDown aria-hidden="true" />)}</button>
                    </th>
                    <th scope="col" className="appr-opt">Step</th>
                    <th scope="col" aria-sort={ariaSort(dateHeading)}>
                      <button type="button" onClick={() => sortBy(dateHeading)}>{tab === "completed" ? "Decided" : "Due"}{sort?.key === dateHeading && (sort.direction === "asc" ? <ArrowUp aria-hidden="true" /> : <ArrowDown aria-hidden="true" />)}</button>
                    </th>
                    <th scope="col">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row) => {
                    const late = tab === "completed" ? null : overdueLabel(row.due_at, now);
                    return (
                      <tr key={row.task_id} data-overdue={late ? "true" : undefined}>
                        <th scope="row">
                          <button type="button" className="appr-doc" onClick={(event) => { opener.current = event.currentTarget; setOpen(row); }}>
                            <span className="appr-number">{row.number}</span> {row.title}
                          </button>
                          <span className="appr-meta"><Person name={row.requester_name} email={row.requester_email} /> · v{row.document_version}</span>
                        </th>
                        <td className="appr-opt" data-label="Type">{TYPE_LABELS[row.document_type] ?? row.document_type}</td>
                        <td className="appr-num" data-label="Amount">{row.amount ? <Money value={row.amount.amount} currency={row.amount.currency} /> : "Not applicable"}</td>
                        <td className="appr-opt" data-label="Step">{stepLabel(row)}</td>
                        <td data-label={tab === "completed" ? "Decided" : "Due"}>
                          {tab === "completed" ? (row.decided_at ? <time dateTime={row.decided_at}>{formatDate(row.decided_at)}</time> : "—") : (
                            <>
                              {row.due_at ? <time dateTime={row.due_at}>{formatDate(row.due_at)}</time> : "No due date"}
                              {late && <span className="appr-late"><CalendarX aria-hidden="true" weight="regular" />{late}</span>}
                            </>
                          )}
                        </td>
                        <td data-label="Status">
                          <InboxStatus word={row.status_word} />
                          {row.on_behalf_of_email && <span className="appr-meta">{tab === "completed" ? "On behalf of" : "Covering"} <Person name={row.on_behalf_of_name} email={row.on_behalf_of_email} /></span>}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
              <footer className="appr-total">
                <span>Showing {rows.length} of {page?.total ?? rows.length}</span>
                {tab === "pending" || tab === "overdue" ? (
                  <span>
                    Total waiting{" "}
                    {page && page.total_amount.length > 0
                      ? page.total_amount.map((total) => <span key={total.currency} className="appr-sum"><Money value={total.amount} currency={total.currency} /></span>)
                      : "Not applicable"}
                  </span>
                ) : null}
                <nav aria-label="Approval pages">
                  {previous.length > 0 && <button type="button" onClick={() => { setCursor(previous.at(-1)); setPrevious((p) => p.slice(0, -1)); }}>Previous page</button>}
                  {page?.next_cursor && <button type="button" onClick={() => { setPrevious((p) => [...p, cursor]); setCursor(page.next_cursor ?? undefined); }}>Next page</button>}
                </nav>
              </footer>
            </>
          )}
        </div>
      </div>
      {open && (
        <DecisionPanel
          key={open.task_id}
          row={open}
          tab={tab}
          next={rows.find((candidate) => candidate.task_id !== open.task_id && tab !== "completed")}
          onOpen={(row) => setOpen(row)}
          refreshRow={refreshRow}
          onClose={() => { setOpen(undefined); queueMicrotask(() => { const el = opener.current; (el?.isConnected ? el : document.getElementById("appr-panel"))?.focus(); }); }}
          onDecided={() => setReload((n) => n + 1)}
        />
      )}
    </section>
  );
}

function formatDate(value: string): string {
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(value));
}
