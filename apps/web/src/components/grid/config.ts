import {
  columnOrderingFeature,
  columnPinningFeature,
  columnResizingFeature,
  columnSizingFeature,
  columnVisibilityFeature,
  rowPaginationFeature,
  rowSelectionFeature,
  rowSortingFeature,
  tableFeatures,
  type ColumnDef,
  type RowData,
} from "@tanstack/react-table";
import type { ReactTable } from "@tanstack/react-table";

export type GridColumnMeta = {
  label: string;
  numeric?: boolean;
};

export const GRID_FEATURES = tableFeatures({
  columnMeta: {} as GridColumnMeta,
  columnOrderingFeature,
  columnPinningFeature,
  columnResizingFeature,
  columnSizingFeature,
  columnVisibilityFeature,
  rowPaginationFeature,
  rowSelectionFeature,
  rowSortingFeature,
});

export type GridFeatures = typeof GRID_FEATURES;
export type GridColumnDef<TData extends RowData> = ColumnDef<GridFeatures, TData, unknown>;
export type GridTable<TData extends RowData> = ReactTable<GridFeatures, TData>;
