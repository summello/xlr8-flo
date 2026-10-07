export type GridDensity = "comfortable" | "compact";

export type GridViewDefinition = {
  columnOrder: string[];
  columnPinning: { end: string[]; start: string[] };
  columnSizing: Record<string, number>;
  columnVisibility: Record<string, boolean>;
  density: GridDensity;
  filter: string;
  id: string;
  name: string;
  sort: { direction: "asc" | "desc"; id: string } | null;
};

export type GridPreferences = {
  density: GridDensity;
  views: GridViewDefinition[];
};

export const DEFAULT_GRID_PREFERENCES: GridPreferences = {
  density: "comfortable",
  views: [],
};

export function gridPreferenceKey(userId: string, gridId: string): string {
  return `xlr8flo.preferences.${encodeURIComponent(userId)}.grid.${encodeURIComponent(gridId)}`;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === "string");
}

function isDensity(value: unknown): value is GridDensity {
  return value === "comfortable" || value === "compact";
}

function isView(value: unknown): value is GridViewDefinition {
  if (!isRecord(value) || !isRecord(value.columnPinning) || !isRecord(value.columnSizing)) {
    return false;
  }
  if (!isRecord(value.columnVisibility)) return false;

  const sort = value.sort;
  const validSort =
    sort === null ||
    (isRecord(sort) &&
      typeof sort.id === "string" &&
      (sort.direction === "asc" || sort.direction === "desc"));

  return (
    typeof value.id === "string" &&
    typeof value.name === "string" &&
    typeof value.filter === "string" &&
    isDensity(value.density) &&
    isStringArray(value.columnOrder) &&
    isStringArray(value.columnPinning.start) &&
    isStringArray(value.columnPinning.end) &&
    Object.values(value.columnSizing).every((size) => typeof size === "number") &&
    Object.values(value.columnVisibility).every((visible) => typeof visible === "boolean") &&
    validSort
  );
}

export function loadGridPreferences(
  storage: Pick<Storage, "getItem">,
  userId: string,
  gridId: string,
): GridPreferences {
  const raw = storage.getItem(gridPreferenceKey(userId, gridId));
  if (raw === null) return DEFAULT_GRID_PREFERENCES;

  try {
    const parsed: unknown = JSON.parse(raw);
    if (!isRecord(parsed) || !isDensity(parsed.density) || !Array.isArray(parsed.views)) {
      return DEFAULT_GRID_PREFERENCES;
    }
    if (!parsed.views.every(isView)) return DEFAULT_GRID_PREFERENCES;
    return { density: parsed.density, views: parsed.views };
  } catch {
    return DEFAULT_GRID_PREFERENCES;
  }
}

export function saveGridPreferences(
  storage: Pick<Storage, "setItem">,
  userId: string,
  gridId: string,
  preferences: GridPreferences,
): void {
  storage.setItem(gridPreferenceKey(userId, gridId), JSON.stringify(preferences));
}
