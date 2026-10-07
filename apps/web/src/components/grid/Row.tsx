import type { Row as TanStackRow, RowData } from "@tanstack/react-table";
import type { CSSProperties, KeyboardEvent } from "react";

import type { GridFeatures, GridTable } from "./config";

export type ActiveGridCell = {
  columnId: string;
  rowIndex: number;
};

type GridRowProps<TData extends RowData> = {
  activeCell: ActiveGridCell;
  absoluteIndex: number;
  onCellFocus: (cell: ActiveGridCell) => void;
  onCellKeyDown: (
    event: KeyboardEvent<HTMLTableCellElement>,
    cell: ActiveGridCell,
    row: TanStackRow<GridFeatures, TData>,
  ) => void;
  onOpen: (row: TData, origin: HTMLElement) => void;
  row: TanStackRow<GridFeatures, TData>;
  style?: CSSProperties;
  table: GridTable<TData>;
};

export default function GridRow<TData extends RowData>({
  absoluteIndex,
  activeCell,
  onCellFocus,
  onCellKeyDown,
  onOpen,
  row,
  style,
  table,
}: GridRowProps<TData>) {
  return (
    <tr
      aria-rowindex={absoluteIndex + 1}
      aria-selected={row.getIsSelected()}
      data-grid-row={absoluteIndex}
      data-selected={row.getIsSelected() || undefined}
      style={style}
    >
      {row.getVisibleCells().map((cell, columnIndex) => {
        const pinned = cell.column.getIsPinned();
        const label = cell.column.columnDef.meta?.label ?? cell.column.id;
        const coordinates = { columnId: cell.column.id, rowIndex: row.index };
        const isActive =
          activeCell.rowIndex === row.index && activeCell.columnId === cell.column.id;
        return (
          <td
            aria-colindex={columnIndex + 1}
            className={cell.column.columnDef.meta?.numeric ? "numeric" : undefined}
            data-column-id={cell.column.id}
            data-label={label}
            data-pinned={pinned || undefined}
            key={cell.id}
            onDoubleClick={(event) => onOpen(row.original, event.currentTarget)}
            onFocus={() => onCellFocus(coordinates)}
            onKeyDown={(event) => onCellKeyDown(event, coordinates, row)}
            style={{
              insetInlineStart:
                pinned === "start" ? cell.column.getStart("start") : undefined,
              width: cell.column.getSize(),
            }}
            tabIndex={isActive ? 0 : -1}
          >
            <table.FlexRender cell={cell} />
          </td>
        );
      })}
    </tr>
  );
}
