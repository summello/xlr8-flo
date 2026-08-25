export type ThemePreference = "light" | "dark" | "system";

export const THEME_STORAGE_KEY = "xlr8flo.theme";

type ThemeRoot = Pick<HTMLElement, "removeAttribute" | "setAttribute">;
type ThemeStorage = Pick<Storage, "getItem" | "removeItem" | "setItem">;

function readStoredPreference(storage?: ThemeStorage): ThemePreference {
  try {
    const value = (storage ?? window.localStorage).getItem(THEME_STORAGE_KEY);
    return value === "light" || value === "dark" ? value : "system";
  } catch {
    return "system";
  }
}

function applyPreference(root: ThemeRoot, preference: ThemePreference): void {
  if (preference === "system") {
    root.removeAttribute("data-theme");
    return;
  }

  root.setAttribute("data-theme", preference);
}

export function initializeTheme(
  root: ThemeRoot = document.documentElement,
  storage?: ThemeStorage,
): ThemePreference {
  const preference = readStoredPreference(storage);
  applyPreference(root, preference);
  return preference;
}

export function setThemePreference(
  preference: ThemePreference,
  root: ThemeRoot = document.documentElement,
  storage?: ThemeStorage,
): void {
  applyPreference(root, preference);

  try {
    const target = storage ?? window.localStorage;
    if (preference === "system") {
      target.removeItem(THEME_STORAGE_KEY);
    } else {
      target.setItem(THEME_STORAGE_KEY, preference);
    }
  } catch {
    // The applied theme remains usable when site storage is unavailable.
  }
}
