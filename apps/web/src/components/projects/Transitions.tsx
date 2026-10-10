import * as Dialog from "@radix-ui/react-dialog";
import { useRef, useState } from "react";
import { apiClient } from "../../api/client";
import { writeHeaders } from "../../features/auth/api";
import Field from "../form/Field";
import Input from "../form/Input";
import { failure, label, type Project, type Transition } from "./api";

export default function Transitions({ project, options, onUpdated, reload }: { project: Project; options: Transition[]; onUpdated: (p: Project) => void; reload: () => void }) {
  const [selected, setSelected] = useState<Transition | null>(null);
  const [reason, setReason] = useState("");
  const [override, setOverride] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [stale, setStale] = useState(false);
  const [busy, setBusy] = useState(false);
  const origin = useRef<HTMLButtonElement | null>(null);
  const submit = async (option: Transition, inputReason = reason, inputOverride = override) => {
    setBusy(true); setError(null); setStale(false);
    try {
      const { data, error, response } = await apiClient.POST("/api/v1/projects/{id}/transitions", {
        params: { path: { id: project.id } }, headers: { ...writeHeaders(), "If-Match": String(project.version) },
        body: { to: option.to, reason: inputReason || null, override: inputOverride },
      });
      if (data) { onUpdated(data); setSelected(null); setReason(""); setOverride(false); }
      else { setError(failure(error, "Changing project status")); setStale(response.status === 409 && error?.checks?.problem === "stale_version"); }
    } catch { setError(failure(undefined, "Changing project status")); }
    finally { setBusy(false); }
  };
  return <section aria-label="Project transitions" className="project-transitions">
    {options.filter(option => option.reachable).map(option => <div key={option.to} className="project-transition">
      <button type="button" disabled={busy || (!option.allowed && !option.override_available)} onClick={e => {
        origin.current = e.currentTarget;
        setReason(""); setOverride(false); setError(null); setStale(false);
        if (option.reason_required || option.override_available) setSelected(option); else void submit(option, "", false);
      }}>{label(option.to)}</button>
      {!option.allowed && <p className="project-blocked">{option.blocked_reasons.join(" ")}</p>}
    </div>)}
    {error && !selected && <p role="alert">{error}</p>}
    {stale && !selected && <button type="button" onClick={reload}>Reload project</button>}
    <Dialog.Root open={selected !== null} onOpenChange={open => { if (!open && !busy) setSelected(null); }}>
      <Dialog.Portal><Dialog.Overlay className="project-dialog-overlay" />
        <Dialog.Content className="project-dialog material-surface" onCloseAutoFocus={e => { e.preventDefault(); origin.current?.focus(); }}>
          <Dialog.Title>{selected ? label(selected.to) : "Change status"}</Dialog.Title>
          <Dialog.Description>Explain this transition. Your reason is retained if the server rejects it.</Dialog.Description>
          {selected?.override_available && <p>{selected.blocked_reasons.join(" ")}</p>}
          <form onSubmit={e => { e.preventDefault(); if (selected) void submit(selected); }}>
            <Field label="Reason" id="transition-reason" required help={selected?.override_available ? "Closure overrides require at least 20 characters and project.close.override permission." : "Explain why this status should change."}>
              <Input autoComplete="off" inputMode="text" required minLength={override ? 20 : 1} value={reason} onChange={e => setReason(e.target.value)} />
            </Field>
            {selected?.override_available && <label className="project-checkbox"><input type="checkbox" checked={override} required={!selected.allowed} onChange={e => setOverride(e.target.checked)} />Override closure</label>}
            {error && <p role="alert">{error}</p>}
            {stale && <button type="button" onClick={reload}>Reload project</button>}
            <div className="project-actions"><button type="submit" disabled={busy}>{busy ? "Saving…" : "Confirm transition"}</button><Dialog.Close disabled={busy}>Cancel</Dialog.Close></div>
          </form>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  </section>;
}
