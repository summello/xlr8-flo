import { describe, expect, it } from "vitest";

import {
  initializeTheme,
  setThemePreference,
  THEME_STORAGE_KEY,
} from "./theme";

function fakeRoot() {
  const attributes = new Map<string, string>();
  return {
    attributes,
    removeAttribute(name: string) {
      attributes.delete(name);
    },
    setAttribute(name: string, value: string) {
      attributes.set(name, value);
    },
  };
}

function fakeStorage() {
  const values = new Map<string, string>();
  return {
    getItem(key: string) {
      return values.get(key) ?? null;
    },
    removeItem(key: string) {
      values.delete(key);
    },
    setItem(key: string, value: string) {
      values.set(key, value);
    },
  };
}

describe("theme preference", () => {
  it("persists an explicit preference and restores it on reload", () => {
    const storage = fakeStorage();
    const firstRoot = fakeRoot();

    setThemePreference("dark", firstRoot, storage);

    expect(firstRoot.attributes.get("data-theme")).toBe("dark");
    expect(storage.getItem(THEME_STORAGE_KEY)).toBe("dark");

    const reloadedRoot = fakeRoot();
    expect(initializeTheme(reloadedRoot, storage)).toBe("dark");
    expect(reloadedRoot.attributes.get("data-theme")).toBe("dark");
  });

  it("uses the unstamped system state when no valid preference is stored", () => {
    const storage = fakeStorage();
    storage.setItem(THEME_STORAGE_KEY, "sepia");
    const root = fakeRoot();
    root.setAttribute("data-theme", "dark");

    expect(initializeTheme(root, storage)).toBe("system");
    expect(root.attributes.has("data-theme")).toBe(false);
  });

  it("removes persisted state when system preference is selected", () => {
    const storage = fakeStorage();
    const root = fakeRoot();
    setThemePreference("light", root, storage);

    setThemePreference("system", root, storage);

    expect(root.attributes.has("data-theme")).toBe(false);
    expect(storage.getItem(THEME_STORAGE_KEY)).toBeNull();
  });

  it("renders correctly when storage access throws", () => {
    const unavailableStorage = {
      getItem(): string | null {
        throw new Error("blocked");
      },
      removeItem(): void {
        throw new Error("blocked");
      },
      setItem(): void {
        throw new Error("blocked");
      },
    };
    const root = fakeRoot();

    expect(() => setThemePreference("light", root, unavailableStorage)).not.toThrow();
    expect(root.attributes.get("data-theme")).toBe("light");

    expect(initializeTheme(root, unavailableStorage)).toBe("system");
    expect(root.attributes.has("data-theme")).toBe(false);
  });
});
