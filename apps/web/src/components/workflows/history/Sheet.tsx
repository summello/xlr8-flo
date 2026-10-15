import * as Dialog from "@radix-ui/react-dialog";
import { X, Circle, ArrowsClockwise } from "@phosphor-icons/react";
import { useState, type ReactNode } from "react";
import type { components } from "../../../api/generated/schema";
import "./sheets.css";
export function Pill({ state, children }: {
    state: components["schemas"]["WorkflowVersionSummary"]["status"] | "neutral";
    children: ReactNode;
}) {
    const Icon = state === "published" ? ArrowsClockwise : Circle;
    return <span className="wfl-pill" data-state={state}><Icon aria-hidden="true" weight="regular"/>{children}</span>;
}
export default function Sheet({ title, children, onClose }: {
    title: string;
    children: ReactNode;
    onClose: () => void;
}) {
    const [origin] = useState(() => document.activeElement as HTMLElement | null);
    return <Dialog.Root open onOpenChange={open => { if (!open)
        onClose(); }}><Dialog.Portal><Dialog.Overlay className="wfl-scrim"/><Dialog.Content className="wfl-sheet" onCloseAutoFocus={event => { event.preventDefault(); origin?.focus(); }}><Dialog.Title>{title}</Dialog.Title><Dialog.Description>Workflow configuration and current routing information.</Dialog.Description><Dialog.Close asChild><button aria-label={`Close ${title}`}><X aria-hidden="true"/>Close</button></Dialog.Close>{children}</Dialog.Content></Dialog.Portal></Dialog.Root>;
}
