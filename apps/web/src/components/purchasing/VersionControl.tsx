import { CaretDown } from "@phosphor-icons/react";
import { useEffect, useId, useRef, useState } from "react";
import { apiClient } from "../../api/client";
import StatusPill from "../status/StatusPill";
import Money from "../ui/Money";
import { failure, formatDate, label, type Problem } from "../requisitions/api";
import { useLoad } from "../requisitions/useLoad";
import type { Po } from "./api";

/** A disclosure beside the title: the current version shown, versions then change orders listed on open. Escape closes it and returns focus to the trigger. */
export default function VersionControl({ po, navigate }: { po: Po; navigate: (href: string) => void }) {
  const [open, setOpen] = useState(false);
  const trigger = useRef<HTMLButtonElement>(null);
  const panel = useId();
  const versions = useLoad(async () => {
    const { data, error } = await apiClient.GET("/api/v1/purchase-orders/{id}/versions", { params: { path: { id: po.id } } });
    if (!data) throw new Error(failure(error as Problem | undefined, "Loading versions"));
    return data;
  }, [po.id]);
  const changes = useLoad(async () => {
    const { data, error } = await apiClient.GET("/api/v1/purchase-orders/{id}/changes", { params: { path: { id: po.id } } });
    if (!data) throw new Error(failure(error as Problem | undefined, "Loading change orders"));
    return data;
  }, [po.id]);
  useEffect(() => {
    if (!open) return;
    const close = (e: KeyboardEvent) => { if (e.key === "Escape") { setOpen(false); trigger.current?.focus(); } };
    document.addEventListener("keydown", close);
    return () => document.removeEventListener("keydown", close);
  }, [open]);
  const go = (id: string) => (e: React.MouseEvent) => { e.preventDefault(); setOpen(false); navigate(`/purchase-orders/${id}`); };
  return <div className="po-version">
    <button ref={trigger} type="button" aria-expanded={open} aria-controls={panel} onClick={() => setOpen(o => !o)}>Version v{po.revision + 1}<CaretDown aria-hidden="true" weight="regular" /></button>
    {open && <div id={panel} className="po-version-panel" role="group" aria-label="Versions and change orders">
      <h3>Versions</h3>
      {versions.error ? <div role="alert"><p>{versions.error}</p><button type="button" onClick={versions.reload}>Try again</button></div> :
        <ul className="vendor-list-plain">{[...(versions.data ?? [])].reverse().map(v => <li key={v.po_id}>
          {v.po_id === po.id ? <strong aria-current="true">v{v.revision + 1}</strong> : <a href={`/purchase-orders/${v.po_id}`} onClick={go(v.po_id)}>v{v.revision + 1}</a>}
          {v.is_current && <span className="req-sub">Current</span>}
          <StatusPill docType="purchase_order" status={v.status as "draft"} />
          <span className="req-sub">{v.issued_at ? `Issued ${formatDate(v.issued_at)}` : "Not issued"} · <Money zero="value" value={v.total} currency={v.currency} /></span></li>)}</ul>}
      <h3>Change orders</h3>
      {changes.error ? <div role="alert"><p>{changes.error}</p><button type="button" onClick={changes.reload}>Try again</button></div> :
        (changes.data ?? []).length === 0 ? <p className="req-sub">No change orders.</p> :
        <ul className="vendor-list-plain">{(changes.data ?? []).map(c => <li key={c.id}><strong>Change order {c.number}</strong><span className="req-sub">{label(c.status)}{c.reason ? `: ${c.reason}` : ""}</span></li>)}</ul>}
    </div>}
  </div>;
}
