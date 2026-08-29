import {
  functionalUpdate,
  useTable,
  type ColumnOrderState,
  type ColumnPinningState,
  type ColumnSizingState,
  type ColumnVisibilityState,
  type RowData,
  type RowSelectionState,
  type SortingState,
  type Updater,
} from "@tanstack/react-table";
import { useVirtualizer } from "@tanstack/react-virtual";
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
  type KeyboardEvent,
} from "react";

import {
  GRID_PAGE_SIZE,
  gridRequestUrl,
  mergeGridParams,
  parseGridParams,
  type GridQuery,
} from "../../lib/grid-params";
import {
  loadGridPreferences,
  saveGridPreferences,
  type GridDensity,
  type GridViewDefinition,
} from "../../lib/grid-preferences";
import Surface from "../ui/Surface";
import DensityToggle from "./DensityToggle";
import GridHeader from "./Header";
import GridRow, { type ActiveGridCell } from "./Row";
import SavedViews from "./SavedViews";
import { GRID_FEATURES, type GridColumnDef } from "./config";
import GridEmpty from "./states/Empty";
import GridError from "./states/Error";
import GridSkeleton from "./states/Skeleton";

const MAX_LOADED_ROWS = GRID_PAGE_SIZE * 4;

type GridPage<TData> = {
  lastCursor: string | null;
  nextCursor: string | null;
  previousCursor: string | null;
  rows: TData[];
  startIndex: number;
  total: number;
};

type GridWindow<TData> = GridPage<TData> & {
  rows: TData[];
};

type LoadMode = "append" | "prepend" | "replace";

type DataGridProps<TData extends RowData> = {
  ariaLabel: string;
  columns: readonly GridColumnDef<TData>[];
  emptyActionLabel: string;
  emptyMessage: string;
  endpoint: string;
  filterLabel: string;
  filterPlaceholder: string;
  getRowId: (row: TData) => string;
  gridId: string;
  onEmptyAction: () => void;
  onOpenRow: (row: TData, origin: HTMLElement) => void;
  parseRow?: (value: unknown) => TData;
  recordLabel: string;
  sortableColumnIds: readonly string[];
  userId: string;
};

class GridLoadError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "GridLoadError";
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function optionalString(value: unknown): value is string | null {
  return value === null || typeof value === "string";
}

function parsePage<TData>(
  value: unknown,
  parseRow?: (row: unknown) => TData,
): GridPage<TData> {
  if (
    !isRecord(value) ||
    !Array.isArray(value.rows) ||
    value.rows.length > GRID_PAGE_SIZE ||
    !Number.isInteger(value.total) ||
    Number(value.total) < 0 ||
    !Number.isInteger(value.startIndex) ||
    Number(value.startIndex) < 0 ||
    !optionalString(value.nextCursor) ||
    !optionalString(value.previousCursor) ||
    !optionalString(value.lastCursor)
  ) {
    throw new GridLoadError("The server returned an invalid paginated grid response.");
  }
  return {
    ...(value as Omit<GridPage<TData>, "rows">),
    rows: parseRow === undefined ? value.rows as TData[] : value.rows.map(parseRow),
  };
}

async function fetchPage<TData>(
  endpoint: string,
  query: GridQuery,
  signal: AbortSignal,
  parseRow?: (value: unknown) => TData,
): Promise<GridPage<TData>> {
  const response = await fetch(gridRequestUrl(endpoint, query), {
    headers: { Accept: "application/json" },
    signal,
  });
  if (!response.ok) {
    throw new GridLoadError(`The server responded with ${response.status}.`);
  }
  return parsePage<TData>(await response.json(), parseRow);
}

function applyUpdater<T>(updater: Updater<T>, current: T): T {
  return functionalUpdate(updater, current);
}

function useNarrowGrid(): boolean {
  const [narrow, setNarrow] = useState(() => window.matchMedia("(max-width: 767px)").matches);

  useEffect(() => {
    const media = window.matchMedia("(max-width: 767px)");
    const update = () => setNarrow(media.matches);
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);

  return narrow;
}

function mergeWindow<TData>(
  current: GridWindow<TData> | null,
  page: GridPage<TData>,
  mode: LoadMode,
): GridWindow<TData> {
  if (current === null || mode === "replace") return page;

  if (mode === "append") {
    const combined = [...current.rows, ...page.rows];
    const overflow = Math.max(0, combined.length - MAX_LOADED_ROWS);
    return {
      ...page,
      previousCursor: current.previousCursor,
      rows: combined.slice(overflow),
      startIndex: current.startIndex + overflow,
    };
  }

  const combined = [...page.rows, ...current.rows];
  return {
    ...current,
    previousCursor: page.previousCursor,
    rows: combined.slice(0, MAX_LOADED_ROWS),
    startIndex: page.startIndex,
  };
}

export default function DataGrid<TData extends RowData>({
  ariaLabel,
  columns,
  emptyActionLabel,
  emptyMessage,
  endpoint,
  filterLabel,
  filterPlaceholder,
  getRowId,
  gridId,
  onEmptyAction,
  onOpenRow,
  parseRow,
  recordLabel,
  sortableColumnIds,
  userId,
}: DataGridProps<TData>) {
  const initialPreferences = useMemo(
    () => loadGridPreferences(localStorage, userId, gridId),
    [gridId, userId],
  );
  const [query, setQuery] = useState(() =>
    parseGridParams(window.location.search, sortableColumnIds),
  );
  const [filterDraft, setFilterDraft] = useState(query.filter);
  const [density, setDensity] = useState<GridDensity>(initialPreferences.density);
  const [views, setViews] = useState<GridViewDefinition[]>(initialPreferences.views);
  const [columnOrder, setColumnOrder] = useState<ColumnOrderState>([]);
  const [columnPinning, setColumnPinning] = useState<ColumnPinningState>({ end: [], start: [] });
  const [columnSizing, setColumnSizing] = useState<ColumnSizingState>({});
  const [columnVisibility, setColumnVisibility] = useState<ColumnVisibilityState>({});
  const [rowSelection, setRowSelection] = useState<RowSelectionState>({});
  const [gridWindow, setGridWindow] = useState<GridWindow<TData> | null>(null);
  const [loadMode, setLoadMode] = useState<LoadMode>("replace");
  const [loading, setLoading] = useState<"initial" | "partial" | null>("initial");
  const [error, setError] = useState<string | null>(null);
  const [activeCell, setActiveCell] = useState<ActiveGridCell>({ columnId: "", rowIndex: 0 });
  const [pendingFocus, setPendingFocus] = useState<{ columnId: string; rowIndex: number } | null>(null);
  const [rowHeight, setRowHeight] = useState(0);
  const scrollRef = useRef<HTMLDivElement>(null);
  const filterRef = useRef<HTMLInputElement>(null);
  const gridOwnsFocus = useRef(false);
  const narrowGrid = useNarrowGrid();

  const sorting = useMemo<SortingState>(
    () =>
      query.sort === null
        ? []
        : [{ desc: query.sort.direction === "desc", id: query.sort.id }],
    [query.sort],
  );

  const table = useTable({
    columns,
    data: gridWindow?.rows ?? [],
    enableMultiSort: false,
    features: GRID_FEATURES,
    getRowId,
    manualPagination: true,
    manualSorting: true,
    onColumnOrderChange: setColumnOrder,
    onColumnPinningChange: setColumnPinning,
    onColumnSizingChange: setColumnSizing,
    onColumnVisibilityChange: setColumnVisibility,
    onRowSelectionChange: setRowSelection,
    onSortingChange: (updater) => {
      const next = applyUpdater(updater, sorting)[0];
      setLoadMode("replace");
      setQuery((current) => ({
        ...current,
        cursor: null,
        sort:
          next === undefined
            ? null
            : { direction: next.desc ? "desc" : "asc", id: next.id },
      }));
    },
    rowCount: gridWindow?.total ?? 0,
    state: {
      columnOrder,
      columnPinning,
      columnSizing,
      columnVisibility,
      rowSelection,
      sorting,
    },
  });

  const rows = table.getRowModel().rows;
  const shouldVirtualize = !narrowGrid && rows.length > GRID_PAGE_SIZE && rowHeight > 0;
  // TanStack Virtual owns imperative scroll measurements; React Compiler intentionally skips it.
  // eslint-disable-next-line react-hooks/incompatible-library
  const rowVirtualizer = useVirtualizer({
    count: shouldVirtualize ? rows.length : 0,
    estimateSize: () => rowHeight,
    getItemKey: (index) => rows[index]?.id ?? index,
    getScrollElement: () => scrollRef.current,
    overscan: 8,
  });

  const commitUrl = useCallback((next: GridQuery, replace = false) => {
    const params = mergeGridParams(window.location.search, next);
    const href = `${window.location.pathname}?${params.toString()}`;
    window.history[replace ? "replaceState" : "pushState"](null, "", href);
  }, []);

  useEffect(() => {
    commitUrl(query, true);
    const controller = new AbortController();
    setLoading(loadMode === "replace" ? "initial" : "partial");
    setError(null);
    fetchPage<TData>(endpoint, query, controller.signal, parseRow)
      .then((page) => {
        setGridWindow((current) => mergeWindow(current, page, loadMode));
        setLoading(null);
      })
      .catch((reason: unknown) => {
        if (controller.signal.aborted) return;
        setLoading(null);
        setError(reason instanceof Error ? reason.message : "The request failed unexpectedly.");
      });
    return () => controller.abort();
  }, [commitUrl, endpoint, loadMode, parseRow, query]);

  const virtualItems = rowVirtualizer.getVirtualItems();
  useEffect(() => {
    const onPopState = () => {
      const next = parseGridParams(window.location.search, sortableColumnIds);
      setFilterDraft(next.filter);
      setLoadMode("replace");
      setQuery(next);
    };
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, [sortableColumnIds]);

  useEffect(() => {
    saveGridPreferences(localStorage, userId, gridId, { density, views });
  }, [density, gridId, userId, views]);

  useLayoutEffect(() => {
    const token = density === "comfortable" ? "--row-h" : "--row-h-compact";
    setRowHeight(
      Number.parseFloat(getComputedStyle(document.documentElement).getPropertyValue(token)),
    );
  }, [density]);

  const visibleColumns = table.getVisibleLeafColumns();
  useEffect(() => {
    if (visibleColumns.length === 0) return;
    if (!visibleColumns.some((column) => column.id === activeCell.columnId)) {
      setActiveCell((current) => ({ ...current, columnId: visibleColumns[0]!.id }));
    }
  }, [activeCell.columnId, visibleColumns]);

  const focusCell = useCallback(
    (absoluteRowIndex: number, columnId: string) => {
      if (gridWindow === null) return;
      const localIndex = absoluteRowIndex - gridWindow.startIndex;
      if (localIndex < 0 || localIndex >= rows.length) return;
      setActiveCell({ columnId, rowIndex: localIndex });
      setPendingFocus({ columnId, rowIndex: absoluteRowIndex });
      if (shouldVirtualize) rowVirtualizer.scrollToIndex(localIndex, { align: "auto" });
    },
    [gridWindow, rowVirtualizer, rows.length, shouldVirtualize],
  );

  useEffect(() => {
    if (pendingFocus === null) return;
    const selector = `[data-grid-row="${pendingFocus.rowIndex}"] [data-column-id="${CSS.escape(pendingFocus.columnId)}"]`;
    const target = scrollRef.current?.querySelector<HTMLElement>(selector);
    if (target === undefined || target === null) return;
    target.focus({ preventScroll: true });
    setPendingFocus(null);
  }, [pendingFocus, virtualItems]);

  const loadCursor = useCallback(
    (cursor: string | null, mode: LoadMode, focus: { columnId: string; rowIndex: number }) => {
      setPendingFocus(focus);
      setLoadMode(mode);
      setQuery((current) => ({ ...current, cursor }));
    },
    [],
  );

  const handleCellKeyDown = (
    event: KeyboardEvent<HTMLTableCellElement>,
    cell: ActiveGridCell,
    row: (typeof rows)[number],
  ) => {
    if (gridWindow === null || visibleColumns.length === 0) return;
    const absoluteIndex = gridWindow.startIndex + cell.rowIndex;
    const columnIndex = visibleColumns.findIndex((column) => column.id === cell.columnId);
    const lastAbsoluteIndex = gridWindow.total - 1;

    if (event.key === "/") {
      event.preventDefault();
      filterRef.current?.focus();
      return;
    }
    if (event.key === " ") {
      event.preventDefault();
      row.toggleSelected();
      return;
    }
    if (event.key === "Enter") {
      event.preventDefault();
      onOpenRow(row.original, event.currentTarget);
      return;
    }
    if (event.key === "Home" && (event.ctrlKey || event.metaKey)) {
      event.preventDefault();
      if (gridWindow.startIndex === 0) focusCell(0, visibleColumns[0]!.id);
      else loadCursor(null, "replace", { columnId: visibleColumns[0]!.id, rowIndex: 0 });
      return;
    }
    if (event.key === "End" && (event.ctrlKey || event.metaKey)) {
      event.preventDefault();
      const lastColumn = visibleColumns.at(-1)!.id;
      if (lastAbsoluteIndex < gridWindow.startIndex + rows.length) {
        focusCell(lastAbsoluteIndex, lastColumn);
      } else if (gridWindow.lastCursor !== null) {
        loadCursor(gridWindow.lastCursor, "replace", {
          columnId: lastColumn,
          rowIndex: lastAbsoluteIndex,
        });
      }
      return;
    }
    if (event.key === "Home") {
      event.preventDefault();
      focusCell(absoluteIndex, visibleColumns[0]!.id);
      return;
    }
    if (event.key === "End") {
      event.preventDefault();
      focusCell(absoluteIndex, visibleColumns.at(-1)!.id);
      return;
    }
    if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
      event.preventDefault();
      const direction = event.key === "ArrowLeft" ? -1 : 1;
      const targetColumn = visibleColumns[Math.max(0, Math.min(visibleColumns.length - 1, columnIndex + direction))];
      if (targetColumn !== undefined) focusCell(absoluteIndex, targetColumn.id);
      return;
    }
    if (event.key === "ArrowDown") {
      event.preventDefault();
      const target = absoluteIndex + 1;
      if (target < gridWindow.startIndex + rows.length) focusCell(target, cell.columnId);
      else if (gridWindow.nextCursor !== null)
        loadCursor(gridWindow.nextCursor, "append", { columnId: cell.columnId, rowIndex: target });
      return;
    }
    if (event.key === "ArrowUp") {
      event.preventDefault();
      const target = absoluteIndex - 1;
      if (target >= gridWindow.startIndex) focusCell(target, cell.columnId);
      else if (target >= 0 && gridWindow.previousCursor !== null)
        loadCursor(gridWindow.previousCursor, "prepend", { columnId: cell.columnId, rowIndex: target });
    }
  };

  const applyView = (view: GridViewDefinition) => {
    setColumnOrder(view.columnOrder);
    setColumnPinning(view.columnPinning);
    setColumnSizing(view.columnSizing);
    setColumnVisibility(view.columnVisibility);
    setDensity(view.density);
    setFilterDraft(view.filter);
    setLoadMode("replace");
    setQuery({ cursor: null, filter: view.filter, sort: view.sort });
  };

  const saveView = (name: string) => {
    const view: GridViewDefinition = {
      columnOrder: [...columnOrder],
      columnPinning: { end: [...columnPinning.end], start: [...columnPinning.start] },
      columnSizing: { ...columnSizing },
      columnVisibility: { ...columnVisibility },
      density,
      filter: query.filter,
      id: crypto.randomUUID(),
      name,
      sort: query.sort,
    };
    setViews((current) => [...current, view]);
  };

  const submitFilter = (event: FormEvent) => {
    event.preventDefault();
    setLoadMode("replace");
    setQuery((current) => ({ ...current, cursor: null, filter: filterDraft.trim() }));
  };

  const renderRows = () => {
    if (error !== null) {
      return (
        <tr className="grid-state-row">
          <td colSpan={Math.max(1, visibleColumns.length)}>
            <GridError
              cause={error}
              onRetry={() => {
                setLoadMode("replace");
                setQuery((current) => ({ ...current }));
              }}
              preserved="Your filter, sort, columns, density, and saved views are preserved. Retry when the service is available."
            />
          </td>
        </tr>
      );
    }
    if (gridWindow === null || loading === "initial") {
      return (
        <tr className="grid-state-row">
          <td colSpan={Math.max(1, visibleColumns.length)}>
            <GridSkeleton columnCount={Math.max(1, visibleColumns.length)} />
          </td>
        </tr>
      );
    }
    if (rows.length === 0) {
      return (
        <tr className="grid-state-row">
          <td colSpan={Math.max(1, visibleColumns.length)}>
            <GridEmpty
              actionLabel={query.filter === "" ? emptyActionLabel : "Clear filter"}
              message={query.filter === "" ? emptyMessage : "No records match this filter."}
              onAction={() => {
                if (query.filter === "") onEmptyAction();
                else {
                  setFilterDraft("");
                  setLoadMode("replace");
                  setQuery((current) => ({ ...current, cursor: null, filter: "" }));
                }
              }}
            />
          </td>
        </tr>
      );
    }

    if (!shouldVirtualize) {
      return rows.map((row) => (
        <GridRow
          absoluteIndex={gridWindow.startIndex + row.index}
          activeCell={activeCell}
          key={row.id}
          onCellFocus={setActiveCell}
          onCellKeyDown={handleCellKeyDown}
          onOpen={onOpenRow}
          row={row}
          table={table}
        />
      ));
    }

    return virtualItems.map((virtualRow) => {
      const row = rows[virtualRow.index]!;
      return (
        <GridRow
          absoluteIndex={gridWindow.startIndex + row.index}
          activeCell={activeCell}
          key={row.id}
          onCellFocus={setActiveCell}
          onCellKeyDown={handleCellKeyDown}
          onOpen={onOpenRow}
          row={row}
          style={{
            position: "absolute",
            transform: `translateY(${virtualRow.start}px)`,
          }}
          table={table}
        />
      );
    });
  };

  const selectedCount = Object.values(rowSelection).filter(Boolean).length;

  return (
    <Surface
      aria-label={`${ariaLabel} controls`}
      className="data-grid-frame"
      data-density={density}
      onBlurCapture={(event) => {
        if (event.relatedTarget instanceof Node && !event.currentTarget.contains(event.relatedTarget)) {
          gridOwnsFocus.current = false;
        }
      }}
      onFocusCapture={() => {
        gridOwnsFocus.current = true;
      }}
      shadow="sm"
    >
      <div className="grid-toolbar">
        <form className="grid-filter" onSubmit={submitFilter}>
          <label htmlFor={`${gridId}-filter`}>{filterLabel}</label>
          <input
            id={`${gridId}-filter`}
            onChange={(event) => setFilterDraft(event.target.value)}
            placeholder={filterPlaceholder}
            ref={filterRef}
            type="search"
            value={filterDraft}
          />
          <button type="submit">Apply filter</button>
        </form>
        <DensityToggle density={density} onChange={setDensity} />
        <details className="grid-keyboard-help">
          <summary>Grid shortcuts</summary>
          <p className="material-shadow" data-shadow="md">Arrows move cells. Home and End move across a row. Control Home and Control End move to grid ends. Space selects. Enter opens. Slash focuses the filter.</p>
        </details>
        <SavedViews
          onApply={applyView}
          onDelete={(id) => setViews((current) => current.filter((view) => view.id !== id))}
          onSave={saveView}
          views={views}
        />
      </div>
      <div
        aria-busy={loading !== null}
        className="data-grid-scroller"
        onScroll={() => {
          if (!gridOwnsFocus.current || document.activeElement !== document.body) return;
          const absoluteIndex = (gridWindow?.startIndex ?? 0) + activeCell.rowIndex;
          setPendingFocus({ columnId: activeCell.columnId, rowIndex: absoluteIndex });
        }}
        ref={scrollRef}
      >
        <table
          aria-label={ariaLabel}
          aria-colcount={visibleColumns.length}
          aria-rowcount={gridWindow?.total ?? 0}
          className="data-grid"
          style={{ width: narrowGrid ? undefined : table.getTotalSize() }}
        >
          <GridHeader table={table} />
          <tbody
            style={
              shouldVirtualize
                ? { height: rowVirtualizer.getTotalSize(), position: "relative" }
                : undefined
            }
          >
            {renderRows()}
          </tbody>
        </table>
      </div>
      <div aria-live="polite" className="grid-footer">
        <span>
          {gridWindow === null
            ? "Loading records"
            : `${gridWindow.total.toLocaleString()} ${recordLabel} · ${rows.length} loaded`}
        </span>
        {loading === "partial" ? <span role="status">Loading more records…</span> : undefined}
        {gridWindow === null || gridWindow.nextCursor === null || loading !== null ? undefined : (
          <button
            onClick={() =>
              loadCursor(gridWindow!.nextCursor, "append", {
                columnId: activeCell.columnId || visibleColumns[0]?.id || "",
                rowIndex: gridWindow!.startIndex + rows.length,
              })
            }
            type="button"
          >
            Load next {GRID_PAGE_SIZE}
          </button>
        )}
      </div>
      {selectedCount === 0 ? undefined : (
        <div className="grid-bulk-bar material-shadow" data-shadow="lg" role="status">
          <span>{selectedCount} selected</span>
          <button onClick={() => setRowSelection({})} type="button">
            Clear selection
          </button>
        </div>
      )}
    </Surface>
  );
}
