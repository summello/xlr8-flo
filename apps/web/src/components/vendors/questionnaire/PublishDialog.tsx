import * as Dialog from "@radix-ui/react-dialog";
import { useRef } from "react";

type Props = { number: number; busy: boolean; onConfirm: () => void; onClose: () => void };

/** The safe button takes first focus; Escape and Cancel keep the draft untouched. */
export default function PublishDialog({ number, busy, onConfirm, onClose }: Props) {
  const cancel = useRef<HTMLButtonElement>(null);
  return (
    <Dialog.Root open onOpenChange={open => { if (!open && !busy) onClose(); }}>
      <Dialog.Portal>
        <Dialog.Overlay className="vq-scrim" />
        <Dialog.Content className="vq-dialog" onOpenAutoFocus={e => { e.preventDefault(); cancel.current?.focus(); }}>
          <Dialog.Title>Publish Version {number}</Dialog.Title>
          <Dialog.Description>A published version never changes. Requests keep the version they were answered against.</Dialog.Description>
          <div className="vq-actions">
            <button type="button" ref={cancel} onClick={onClose} disabled={busy}>Cancel</button>
            <button type="button" aria-busy={busy} onClick={onConfirm}>Publish Version {number}</button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
