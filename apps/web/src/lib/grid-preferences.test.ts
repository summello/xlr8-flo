import { describe, expect, it } from "vitest";

import {
  DEFAULT_GRID_PREFERENCES,
  gridPreferenceKey,
  loadGridPreferences,
  saveGridPreferences,
  type GridPreferences,
} from "./grid-preferences";

function memoryStorage(): Storage {
  const values = new Map<string, string>();
  return {
    clear: () => values.clear(),
    getItem: (key) => values.get(key) ?? null,
    key: (index) => [...values.keys()][index] ?? null,
    get length() {
      return values.size;
    },
    removeItem: (key) => values.delete(key),
    setItem: (key, value) => values.set(key, value),
  };
}

const preferences: GridPreferences = {
  density: "compact",
  views: [
    {
      columnOrder: ["reference", "name", "status"],
      columnPinning: { end: [], start: ["reference"] },
      columnSizing: { name: 240 },
      columnVisibility: { owner: false },
      density: "compact",
      filter: "plant",
      id: "plant-view",
      name: "Plant work",
      sort: { direction: "desc", id: "name" },
    },
  ],
};

describe("personal grid preferences", () => {
  it("keys preferences by both user and grid without touching shared record data", () => {
    expect(gridPreferenceKey("user/a", "projects grid")).toBe(
      "xlr8flo.preferences.user%2Fa.grid.projects%20grid",
    );
    expect(gridPreferenceKey("user/b", "projects grid")).not.toBe(
      gridPreferenceKey("user/a", "projects grid"),
    );
  });

  it("round-trips every required saved-view field and reports missing fields as invalid", () => {
    const storage = memoryStorage();
    saveGridPreferences(storage, "user-1", "projects", preferences);
    expect(loadGridPreferences(storage, "user-1", "projects")).toEqual(preferences);

    const missingDensity = { ...preferences.views[0] } as Record<string, unknown>;
    delete missingDensity.density;
    storage.setItem(
      gridPreferenceKey("user-1", "projects"),
      JSON.stringify({ density: "compact", views: [missingDensity] }),
    );
    expect(loadGridPreferences(storage, "user-1", "projects")).toEqual(
      DEFAULT_GRID_PREFERENCES,
    );
  });

  it("falls back safely for absent or malformed personal preferences", () => {
    const storage = memoryStorage();
    expect(loadGridPreferences(storage, "user-1", "projects")).toEqual(
      DEFAULT_GRID_PREFERENCES,
    );
    storage.setItem(gridPreferenceKey("user-1", "projects"), "not json");
    expect(loadGridPreferences(storage, "user-1", "projects")).toEqual(
      DEFAULT_GRID_PREFERENCES,
    );
  });
});
