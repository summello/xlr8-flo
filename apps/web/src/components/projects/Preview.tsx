import * as Dialog from "@radix-ui/react-dialog";
import { X } from "@phosphor-icons/react";
import StatusPill from "../status/StatusPill";
import Money from "../ui/Money";
import { label, type Project } from "./api";

export default function ProjectPreview({ project, close, open, origin }: {
  project: Project | null; close: () => void; open: (id: string) => void; origin: HTMLElement | null;
}) {
  return <Dialog.Root open={project !== null} onOpenChange={isOpen => { if (!isOpen) close(); }}>
    <Dialog.Portal><Dialog.Overlay className="grid-sheet-overlay glass-sheet-backdrop" />
      <Dialog.Content className="grid-record-sheet material-surface material-shadow" data-shadow="lg" onCloseAutoFocus={e => { e.preventDefault(); origin?.focus(); }}>
        <Dialog.Title>{project?.name}</Dialog.Title>
        <Dialog.Description>Project summary. Open the project to work with its full details.</Dialog.Description>
        {project && <><dl className="project-facts">
          <div><dt>Number</dt><dd>{project.number}</dd></div>
          <div><dt>Status</dt><dd><StatusPill docType="project" status={project.status} /></dd></div>
          <div><dt>Business unit</dt><dd>{project.bu_name ?? project.bu_id}</dd></div>
          <div><dt>Owner</dt><dd>{project.owner_id}</dd></div>
          <div><dt>Health</dt><dd>{label(project.health)}</dd></div>
          <div><dt>Available</dt><dd><Money value={project.available ?? null} currency={project.currency} /></dd></div>
        </dl><button onClick={() => open(project.id)}>Open project</button></>}
        <Dialog.Close aria-label="Close project preview" className="grid-sheet-close"><X aria-hidden="true" weight="regular" /></Dialog.Close>
      </Dialog.Content>
    </Dialog.Portal>
  </Dialog.Root>;
}
