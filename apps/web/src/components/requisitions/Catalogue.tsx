import { useEffect, useState } from "react";
import { apiClient } from "../../api/client";
import Money from "../ui/Money";
import { formatDate, type CatalogueItem } from "./api";

/** REQ-007/008. A failing search leaves the rest of the form working (partial state). */
export default function Catalogue({ buId, onAdd }: { buId: string | null; onAdd: (item: CatalogueItem) => void }) {
  const [q, setQ] = useState("");
  const [rows, setRows] = useState<CatalogueItem[] | null>(null);
  const [failed, setFailed] = useState(false);
  const [searching, setSearching] = useState(false);
  useEffect(() => {
    if (!buId) return;
    let active = true;
    // The request is started by this effect; searching is cleared only from its response.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setSearching(true);
    const timer = setTimeout(() => {
      apiClient.GET("/api/v1/catalogue", { params: { query: { bu_id: buId, q, limit: 4 } } })
        .then(({ data }) => { if (!active) return; setFailed(!data); setRows(data?.rows ?? null); setSearching(false); })
        .catch(() => { if (active) { setFailed(true); setSearching(false); } });
    }, 250);
    return () => { active = false; clearTimeout(timer); };
  }, [buId, q]);
  return <section className="req-card" aria-labelledby="catalogue-title">
    <header className="req-card-head"><h2 id="catalogue-title">Catalogue Suggestions</h2></header>
    <label className="t-label" htmlFor="catalogue-search">Find in the catalogue</label>
    <input id="catalogue-search" type="search" value={q} onChange={e => setQ(e.target.value)} disabled={!buId} aria-describedby="catalogue-help" />
    <p className="field-help" id="catalogue-help">{buId ? "Previously requested, sourced or bought within your scope." : "Choose a project or business unit to see suggestions."}</p>
    {searching && rows === null && !failed ? <p className="req-skeleton req-skeleton-short" role="status" aria-label="Loading suggestions" /> : null}
    {searching && rows !== null && !failed ? <p role="status" className="req-refreshing">Searching the catalogue…</p> : null}
    {failed ? <div role="alert"><p><strong>Could not load suggestions</strong></p><p>You can still add lines by hand.</p></div> :
      rows !== null && (rows.length === 0 ? <p>Nothing in the catalogue matches. Add the line by hand.</p> :
        <ul className="req-suggestions">{rows.map(item => <li key={item.id}>
          <div><strong>{item.description}</strong>
            <span>{item.line_type === "item" ? "Item" : "Service"} · {item.last_event === "purchased" ? "bought" : item.last_event} {formatDate(item.last_event_at)}{item.last_vendor_name ? ` from ${item.last_vendor_name}` : ""}</span>
            <span><Money zero="value" value={item.last_unit_price} currency={item.currency} /> per {item.uom_code}</span></div>
          <button type="button" aria-label={`Add ${item.description}`} onClick={() => onAdd(item)}>Add</button></li>)}</ul>)}
  </section>;
}
