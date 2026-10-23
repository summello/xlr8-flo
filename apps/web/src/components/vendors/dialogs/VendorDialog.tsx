import * as Dialog from "@radix-ui/react-dialog";
import { ArrowCounterClockwise, XCircle } from "@phosphor-icons/react";
import { useEffect, useId, useRef, useState } from "react";
import { apiClient } from "../../../api/client";
import { failure, mutationHeaders } from "../../requisitions/api";
import type { Unit } from "../api";

export type DialogKind = "suspend" | "reactivate" | "availability" | "disable" | "enable";

type Props = { kind: DialogKind; vendorId: string; unit: Unit | null; onClose: () => void; onDone: () => void };

const TEXT: Record<DialogKind, { title: string; intro: string; confirm: (unit?: string) => string; approval: boolean; undo: string }> = {
  suspend: { title: "Suspend Vendor", intro: "A suspended vendor cannot be chosen for new RFQs or purchase orders. Existing records keep the vendor as it was.", confirm: () => "Request Suspension", approval: true, undo: "Yes, by requesting reactivation." },
  reactivate: { title: "Reactivate Vendor", intro: "A reactivated vendor can be chosen for new RFQs and purchase orders again.", confirm: () => "Request Reactivation", approval: true, undo: "Yes, by requesting suspension." },
  availability: { title: "Change Availability", intro: "Availability decides which units can use this vendor.", confirm: () => "Request Change", approval: true, undo: "Yes, by requesting another change." },
  disable: { title: "Disable Here", intro: "The vendor stays global but cannot be chosen in this unit. History is kept.", confirm: u => `Disable in ${u}`, approval: false, undo: "Yes, with Enable Here." },
  enable: { title: "Enable Here", intro: "The vendor can be chosen in this unit again.", confirm: u => `Enable in ${u}`, approval: false, undo: "Yes, with Disable Here." },
};

/** One dialog for all five actions: the safe button takes first focus, the reason is required, and every refusal keeps what was typed. */
export default function VendorDialog({ kind, vendorId, unit, onClose, onDone }: Props) {
  const t = TEXT[kind];
  const needsReason = kind !== "enable";
  const cancel = useRef<HTMLButtonElement>(null);
  const [opener] = useState(() => document.activeElement as HTMLElement | null);
  const reasonField = useRef<HTMLTextAreaElement>(null);
  const uid = useId();
  const [reason, setReason] = useState("");
  const [fieldError, setFieldError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [availability, setAvailability] = useState<"global" | "restricted">("global");
  const [units, setUnits] = useState<Unit[]>([]);
  const [picked, setPicked] = useState<string[]>([]);
  const [key] = useState(() => crypto.randomUUID());
  const [unitsError, setUnitsError] = useState<string | null>(null);
  const [unitsLoading, setUnitsLoading] = useState(kind === "availability");
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    if (kind !== "availability") return;
    let live = true;
    void (async () => {
      const all: Unit[] = [];
      let cursor: string | undefined;
      do {
        const { data, error } = await apiClient.GET("/api/v1/org/units", { params: { query: { page_size: 100, active: true, ...(cursor ? { cursor } : {}) } } });
        if (!data) { if (live) { setUnitsError(failure(error, "Loading the units")); setUnitsLoading(false); } return; }
        all.push(...data.rows);
        cursor = data.next_cursor ?? undefined;
      } while (cursor);
      if (live) { setUnits(all); setUnitsLoading(false); }
    })();
    return () => { live = false; };
  }, [kind, retry]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (needsReason && !reason.trim()) { setFieldError("Give a reason."); reasonField.current?.focus(); return; }
    setFieldError(null); setError(null); setBusy(true);
    const headers = mutationHeaders(key);
    const params = { path: { id: vendorId, unit_id: unit?.id ?? "" } };
    const res = kind === "disable" ? await apiClient.POST("/api/v1/vendors/{id}/units/{unit_id}/disable", { params, headers, body: { reason } })
      : kind === "enable" ? await apiClient.POST("/api/v1/vendors/{id}/units/{unit_id}/enable", { params, headers })
        : await apiClient.POST("/api/v1/vendors/{id}/changes", { params: { path: { id: vendorId } }, headers, body: { kind, reason, ...(kind === "availability" ? { payload: { availability, unit_ids: availability === "restricted" ? picked : [] } } : {}) } });
    setBusy(false);
    if (res.error) { setError(failure(res.error as { detail?: string | null }, t.title)); return; }
    onDone();
  }

  const unitName = unit?.name ?? "this unit";
  return (
    <Dialog.Root open onOpenChange={open => { if (!open && !busy) onClose(); }}>
      <Dialog.Portal>
        <Dialog.Overlay className="vq-scrim" />
        <Dialog.Content className="vq-dialog vendor-dialog" onOpenAutoFocus={e => { e.preventDefault(); cancel.current?.focus(); }} onCloseAutoFocus={e => { e.preventDefault(); opener?.focus(); }}>
          <Dialog.Title>{t.title}</Dialog.Title>
          <Dialog.Description>{t.intro}</Dialog.Description>
          <form onSubmit={submit} noValidate>
            <dl className="vendor-facts" aria-label="Consequences">
              <dt>Open RFQs</dt><dd>0</dd>
              <dt>Issued purchase orders</dt><dd>0</dd>
              <dt>Approval needed</dt><dd>{t.approval ? "Yes" : "No, it takes effect at once"}</dd>
              <dt>Can be undone</dt><dd>{t.undo}</dd>
            </dl>
            {kind === "availability" && <fieldset className="vendor-dialog-units">
              <legend>Available to</legend>
              <label><input type="radio" name={`${uid}-a`} checked={availability === "global"} onChange={() => setAvailability("global")} /> Global</label>
              <label><input type="radio" name={`${uid}-a`} checked={availability === "restricted"} onChange={() => setAvailability("restricted")} /> Selected Units</label>
              {availability === "restricted" && unitsLoading && <p role="status">Loading units…</p>}
              {availability === "restricted" && !unitsLoading && !unitsError && units.length === 0 && <p role="status">There are no active units to choose from.</p>}
              {availability === "restricted" && unitsError && <div role="alert" className="vendor-dialog-error"><p>{unitsError}</p><button type="button" onClick={() => { setUnitsError(null); setUnitsLoading(true); setRetry(n => n + 1); }}>Try again</button></div>}
              {availability === "restricted" && units.map(u => <label key={u.id}><input type="checkbox" checked={picked.includes(u.id)} onChange={e => setPicked(p => e.target.checked ? [...p, u.id] : p.filter(x => x !== u.id))} /> {u.name}</label>)}
            </fieldset>}
            {needsReason && <div className="req-field" data-invalid={fieldError ? "true" : undefined}>
              <label htmlFor={`${uid}-reason`}>Reason (required)</label>
              <textarea id={`${uid}-reason`} ref={reasonField} required rows={3} maxLength={500} value={reason} aria-invalid={fieldError ? true : undefined} aria-describedby={fieldError ? `${uid}-err` : undefined} onChange={e => setReason(e.target.value)} />
              {fieldError && <p id={`${uid}-err`} className="vendor-dialog-error">{fieldError}</p>}
            </div>}
            {error && <p role="alert" className="vendor-dialog-error">{error}</p>}
            <div className="vq-actions">
              <button type="button" ref={cancel} onClick={onClose} disabled={busy}>Cancel</button>
              <button type="submit" aria-busy={busy} disabled={busy || (availability === "restricted" && kind === "availability" && (unitsLoading || !!unitsError))} className={kind === "suspend" ? "vendor-danger-confirm" : kind === "reactivate" ? "vendor-primary-confirm" : undefined}>
                {kind === "suspend" && <XCircle aria-hidden="true" />}{kind === "reactivate" && <ArrowCounterClockwise aria-hidden="true" />}{t.confirm(unitName)}
              </button>
            </div>
          </form>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
