import * as Dialog from "@radix-ui/react-dialog";
import { createColumnHelper } from "@tanstack/react-table";
import { X } from "@phosphor-icons/react";
import { useRef, useState } from "react";

import DataGrid from "../../components/grid/DataGrid";
import { GRID_FEATURES } from "../../components/grid/config";
import StatusPill from "../../components/status/StatusPill";
import type { StatusFor } from "../../components/status/map";

export type ProjectGridRow = {
  id: string;
  name: string;
  owner: string;
  reference: string;
  status: StatusFor<"project">;
  units: number;
};

const columnHelper = createColumnHelper<typeof GRID_FEATURES, ProjectGridRow>();
const columns = columnHelper.columns([
  columnHelper.accessor("reference", {
    header: "Reference",
    meta: { label: "Reference" },
  }),
  columnHelper.accessor("name", {
    header: "Project",
    meta: { label: "Project" },
  }),
  columnHelper.accessor("status", {
    cell: ({ getValue }) => <StatusPill docType="project" status={getValue()} />,
    header: "Status",
    meta: { label: "Status" },
  }),
  columnHelper.accessor("owner", {
    header: "Owner",
    meta: { label: "Owner" },
  }),
  columnHelper.accessor("units", {
    cell: ({ getValue }) => getValue().toLocaleString(),
    header: "Units",
    meta: { label: "Units", numeric: true },
  }),
]);

export default function GridGallery() {
  const [openRow, setOpenRow] = useState<ProjectGridRow | null>(null);
  const [announcement, setAnnouncement] = useState("");
  const originRef = useRef<HTMLElement | null>(null);
  const fixture = new URLSearchParams(window.location.search).get("fixture");
  const endpoint = fixture === null ? "/api/_dev/grid" : `/api/_dev/grid?fixture=${fixture}`;

  return (
    <>
      <DataGrid
        ariaLabel="Projects data grid"
        columns={columns}
        emptyActionLabel="Add your first project — press C"
        emptyMessage="No projects exist in this view."
        endpoint={endpoint}
        filterLabel="Filter records"
        filterPlaceholder="Filter server results"
        getRowId={(row) => row.id}
        gridId="project-gallery"
        onEmptyAction={() => setAnnouncement("Create project action is ready.")}
        onOpenRow={(row, origin) => {
          originRef.current = origin;
          setOpenRow(row);
        }}
        recordLabel="server records"
        sortableColumnIds={["reference", "name", "status", "owner", "units"]}
        userId="fixture-user"
      />
      <p aria-live="polite" className="visually-hidden">
        {announcement}
      </p>
      <Dialog.Root
        onOpenChange={(open) => {
          if (!open) setOpenRow(null);
        }}
        open={openRow !== null}
      >
        <Dialog.Portal>
          <Dialog.Overlay className="grid-sheet-overlay glass-sheet-backdrop" />
          <Dialog.Content
            aria-describedby="grid-record-description"
            className="grid-record-sheet material-surface material-shadow"
            data-shadow="lg"
            onCloseAutoFocus={(event) => {
              event.preventDefault();
              originRef.current?.focus();
            }}
          >
            <Dialog.Title>{openRow?.name}</Dialog.Title>
            <Dialog.Description id="grid-record-description">
              Project record preview opened from the data grid.
            </Dialog.Description>
            {openRow === null ? undefined : (
              <dl>
                <div>
                  <dt>Reference</dt>
                  <dd>{openRow.reference}</dd>
                </div>
                <div>
                  <dt>Status</dt>
                  <dd><StatusPill docType="project" status={openRow.status} /></dd>
                </div>
                <div>
                  <dt>Owner</dt>
                  <dd>{openRow.owner}</dd>
                </div>
              </dl>
            )}
            <Dialog.Close aria-label="Close project preview" className="grid-sheet-close">
              <X aria-hidden="true" weight="regular" />
            </Dialog.Close>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </>
  );
}
