import { ArrowsDownUp, CaretDown, CaretUp } from "@phosphor-icons/react";
import type { RowData } from "@tanstack/react-table";
import type { MouseEventHandler, TouchEventHandler } from "react";

import ColumnMenu from "./ColumnMenu";
import type { GridTable } from "./config";

type GridHeaderProps<TData extends RowData> = {
  table: GridTable<TData>;
};

function tokenPixels(token: string): number {
  return Number.parseFloat(getComputedStyle(document.documentElement).getPropertyValue(token));
}

export default function GridHeader<TData extends RowData>({ table }: GridHeaderProps<TData>) {
  const visibleColumns = table.getVisibleLeafColumns();

  const moveColumn = (columnId: string, offset: -1 | 1) => {
    const ids = table.getAllLeafColumns().map((column) => column.id);
    const index = ids.indexOf(columnId);
    const target = index + offset;
    if (index < 0 || target < 0 || target >= ids.length) return;
    const next = [...ids];
    [next[index], next[target]] = [next[target]!, next[index]!];
    table.setColumnOrder(next);
  };

  const resizeColumn = (columnId: string, direction: -1 | 1) => {
    const column = table.getColumn(columnId);
    if (column === undefined) return;
    const step = tokenPixels("--space-2");
    table.setColumnSizing((current) => ({
      ...current,
      [columnId]: column.getSize() + direction * step,
    }));
  };

  return (
    <thead className="data-grid-header glass-grid-header">
      {table.getHeaderGroups().map((headerGroup) => (
        <tr key={headerGroup.id}>
          {headerGroup.headers.map((header) => {
            const column = header.column;
            const sort = column.getIsSorted();
            const label = column.columnDef.meta?.label ?? column.id;
            const pinned = column.getIsPinned();
            const visibleIndex = visibleColumns.findIndex((candidate) => candidate.id === column.id);
            const resizeHandler = header.getResizeHandler();
            return (
              <th
                aria-sort={
                  column.getCanSort()
                    ? sort === "asc"
                      ? "ascending"
                      : sort === "desc"
                        ? "descending"
                        : "none"
                    : undefined
                }
                className={column.columnDef.meta?.numeric ? "numeric" : undefined}
                data-pinned={pinned || undefined}
                key={header.id}
                scope="col"
                style={{
                  insetInlineStart: pinned === "start" ? column.getStart("start") : undefined,
                  width: column.getSize(),
                }}
              >
                <div className="grid-header-content">
                  {header.isPlaceholder ? undefined : column.getCanSort() ? (
                    <button
                      className="grid-sort-button"
                      onClick={column.getToggleSortingHandler()}
                      type="button"
                    >
                      <table.FlexRender header={header} />
                      {sort === "asc" ? (
                        <CaretUp aria-hidden="true" weight="regular" />
                      ) : sort === "desc" ? (
                        <CaretDown aria-hidden="true" weight="regular" />
                      ) : (
                        <ArrowsDownUp aria-hidden="true" weight="regular" />
                      )}
                    </button>
                  ) : (
                    <span className="grid-header-label">
                      <table.FlexRender header={header} />
                    </span>
                  )}
                  <ColumnMenu
                    canHide={column.getCanHide() && visibleColumns.length > 1}
                    canMoveNext={visibleIndex >= 0 && visibleIndex < visibleColumns.length - 1}
                    canMovePrevious={visibleIndex > 0}
                    isPinned={pinned === "start"}
                    label={label}
                    onHide={() => column.toggleVisibility(false)}
                    onMoveNext={() => moveColumn(column.id, 1)}
                    onMovePrevious={() => moveColumn(column.id, -1)}
                    onPin={() => column.pin(pinned === "start" ? false : "start")}
                    onResize={(direction) => resizeColumn(column.id, direction)}
                  />
                </div>
                {column.getCanResize() ? (
                  <button
                    aria-label={`Resize ${label} column`}
                    aria-orientation="vertical"
                    aria-valuenow={column.getSize()}
                    aria-valuetext={`${column.getSize()} pixels wide`}
                    className="grid-resizer"
                    onKeyDown={(event) => {
                      if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
                      event.preventDefault();
                      resizeColumn(column.id, event.key === "ArrowLeft" ? -1 : 1);
                    }}
                    onMouseDown={resizeHandler as MouseEventHandler<HTMLButtonElement>}
                    onTouchStart={resizeHandler as TouchEventHandler<HTMLButtonElement>}
                    role="separator"
                    type="button"
                  />
                ) : undefined}
              </th>
            );
          })}
        </tr>
      ))}
    </thead>
  );
}
