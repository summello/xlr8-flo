/** Registry of dirty-form guards that in-app navigation and tab close consult (UX-003). */
type Guard = () => boolean;
const guards = new Set<Guard>();
export const LEAVE_MESSAGE = "You have unsaved changes. Leave this page and discard them?";

function beforeUnload(event: BeforeUnloadEvent) {
  event.preventDefault();
  event.returnValue = "";
}

/** Register a guard returning true while its form is dirty. Returns the unregister function. */
export function registerGuard(isDirty: Guard): () => void {
  guards.add(isDirty);
  if (guards.size === 1) window.addEventListener("beforeunload", beforeUnload);
  return () => {
    guards.delete(isDirty);
    if (guards.size === 0) window.removeEventListener("beforeunload", beforeUnload);
  };
}

export function isDirty(): boolean {
  return [...guards].some(guard => guard());
}

/** True when navigation may go ahead: nothing is dirty, or the user confirmed leaving. */
export function confirmLeave(): boolean {
  return !isDirty() || window.confirm(LEAVE_MESSAGE);
}

// History position (UX-003). Every entry the app creates carries `{ idx }` in history.state, so a cancelled
// Back or Forward can step back to where the user was with history.go(): the forward entries survive,
// which a pushState after the pop would truncate.
let currentIdx = 0;
let restoring = false;
let installed = false;

const withIdx = (state: unknown, idx: number) =>
  ({ ...(typeof state === "object" && state !== null ? state : {}), idx });

/** Seed the first entry with idx and stamp every later pushState/replaceState, whoever calls them. */
export function installHistoryIndex(): void {
  if (installed) return;
  installed = true;
  const push = window.history.pushState.bind(window.history);
  const replace = window.history.replaceState.bind(window.history);
  const seeded = (window.history.state as { idx?: unknown } | null)?.idx;
  currentIdx = typeof seeded === "number" ? seeded : 0;
  replace(withIdx(window.history.state, currentIdx), "");
  window.history.pushState = (state, title, url) => { currentIdx += 1; push(withIdx(state, currentIdx), title, url); };
  window.history.replaceState = (state, title, url) => replace(withIdx(state, currentIdx), title, url);
}

/** The one-shot popstate that history.go() fires after a cancelled leave: ignore it. */
export function consumeRestoring(): boolean {
  const was = restoring;
  restoring = false;
  return was;
}

/** Decide a Back/Forward landing on `event.state.idx`. True: go ahead and route. False: the user stayed. */
export function settlePop(state: unknown): boolean {
  const idx = (state as { idx?: unknown } | null)?.idx;
  if (confirmLeave()) {
    if (typeof idx === "number") currentIdx = idx;
    return true;
  }
  if (typeof idx === "number" && idx !== currentIdx) {
    restoring = true;
    window.history.go(currentIdx - idx);
  }
  return false;
}
