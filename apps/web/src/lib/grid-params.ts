export const GRID_PAGE_SIZE = 50;

export type GridSortDirection = "asc" | "desc";

export type GridQuery = {
  cursor: string | null;
  filter: string;
  sort: {
    direction: GridSortDirection;
    id: string;
  } | null;
};

export const EMPTY_GRID_QUERY: GridQuery = {
  cursor: null,
  filter: "",
  sort: null,
};

function paramsFrom(source: string | URLSearchParams): URLSearchParams {
  return typeof source === "string"
    ? new URLSearchParams(source.startsWith("?") ? source.slice(1) : source)
    : new URLSearchParams(source);
}

export function parseGridParams(
  source: string | URLSearchParams,
  sortableColumnIds: readonly string[],
): GridQuery {
  const params = paramsFrom(source);
  const sortId = params.get("sort");
  const direction = params.get("direction");
  const hasValidSort =
    sortId !== null &&
    sortableColumnIds.includes(sortId) &&
    (direction === "asc" || direction === "desc");

  return {
    cursor: params.get("cursor") || null,
    filter: params.get("filter")?.trim() ?? "",
    sort: hasValidSort ? { direction, id: sortId } : null,
  };
}

export function mergeGridParams(
  source: string | URLSearchParams,
  query: GridQuery,
): URLSearchParams {
  const params = paramsFrom(source);
  params.set("page_size", String(GRID_PAGE_SIZE));

  if (query.cursor === null) params.delete("cursor");
  else params.set("cursor", query.cursor);

  if (query.filter === "") params.delete("filter");
  else params.set("filter", query.filter);

  if (query.sort === null) {
    params.delete("sort");
    params.delete("direction");
  } else {
    params.set("sort", query.sort.id);
    params.set("direction", query.sort.direction);
  }

  return params;
}

export function gridRequestUrl(endpoint: string, query: GridQuery): string {
  const url = new URL(endpoint, window.location.origin);
  url.search = mergeGridParams(url.searchParams, query).toString();
  return url.toString();
}
