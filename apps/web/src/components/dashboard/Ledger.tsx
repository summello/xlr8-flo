import { useCallback, useEffect, useState } from "react";
import type { components } from "../../api/generated/schema";
import { apiClient } from "../../api/client";
import { failure, label } from "../projects/api";
import Money from "../ui/Money";
import { scaled } from "./decimal";
type Bucket = components["schemas"]["LedgerBucket"];
export default function Ledger({ id, bucket }: { id: string; bucket?: Bucket }) {
  const [page, setPage] = useState<components["schemas"]["LedgerPage"] | null>(null);
  const [reconcile, setReconcile] = useState<components["schemas"]["ReconciliationRead"] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(async (cursor?: string) => {
    const results = await Promise.allSettled([
      apiClient.GET("/api/v1/projects/{project_id}/ledger", { params: { path: { project_id: id }, query: { bucket, cursor } } }),
      apiClient.GET("/api/v1/projects/{project_id}/balance/reconcile", { params: { path: { project_id: id } } }),
    ]);
    const problems: string[] = [];
    const entries = results[0]; const check = results[1];
    if (entries.status === "fulfilled" && entries.value.data) setPage(entries.value.data);
    else problems.push(failure(entries.status === "fulfilled" ? entries.value.error : undefined, "Loading ledger"));
    if (check.status === "fulfilled" && check.value.data) setReconcile(check.value.data);
    else problems.push(failure(check.status === "fulfilled" ? check.value.error : undefined, "Checking reconciliation"));
    setError(problems.join(" ") || null);
  }, [id, bucket]);
  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => { void load(); }, [load]);
  const linkedEntry = new URLSearchParams(window.location.search).get("entry");
  const drift = reconcile ? Object.entries(reconcile.difference_by_bucket).filter(([, value]) => scaled(value) !== 0n) : [];
  return <section id="ledger" aria-label="Ledger entries"><h2>Ledger Entries{bucket ? `: ${label(bucket)}` : ""}</h2>
    {linkedEntry && <p>Linked entry {linkedEntry}.{page && !page.entries.some(entry => String(entry.id) === linkedEntry) && " This entry is outside the current page. Browse the ledger pages to locate it."}</p>}
    {!page && !error && <p role="status">Loading ledger entries…</p>}
    {error && <div role="alert"><p>{error}</p><button onClick={() => void load()}>Retry ledger</button></div>}
    {reconcile && <div role="status">{!drift.length ? "Reconciles to the ledger" : <>Does not reconcile to the ledger.{drift.map(([key, value]) => <p key={key}>{label(key)} difference: <Money value={value} currency={reconcile.balance.currency} /></p>)}</>}</div>}
    {page && (!page.entries.length ? <p>No ledger entries for this filter.</p> : <><div className="dashboard-table-wrap"><table><caption>Posted bucket movements</caption><thead><tr><th>Entry</th><th>Bucket / amount</th><th>Source</th><th>Actor / reason</th><th>Lineage</th></tr></thead><tbody>{page.entries.map(entry => <tr id={`entry-${entry.id}`} key={entry.id}><th scope="row">{entry.id} · {entry.entry_type}</th><td data-label="Bucket / amount">{entry.bucket}<Money value={String(entry.amount)} currency={entry.currency} /></td><td data-label="Source">{entry.source_type} {entry.source_id ?? "Manual"}</td><td data-label="Actor / reason">{entry.actor_id}<p>{entry.reason ?? "No reason recorded"}</p></td><td data-label="Lineage">{entry.reverses_entry_id && <a href={`?bucket=all&entry=${entry.reverses_entry_id}#ledger`}>Reverses entry {entry.reverses_entry_id}</a>}{entry.releases_entry_id && <a href={`?bucket=all&entry=${entry.releases_entry_id}#ledger`}>Releases entry {entry.releases_entry_id}</a>}</td></tr>)}</tbody></table></div><button onClick={() => void load()}>First page</button>{page.next_cursor && <button onClick={() => void load(page.next_cursor!)}>Next ledger page</button>}</>)}
  </section>;
}
