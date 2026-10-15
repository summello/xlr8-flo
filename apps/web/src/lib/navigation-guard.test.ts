import { afterEach, describe, expect, it, vi } from "vitest";
import { confirmLeave, isDirty, registerGuard } from "./navigation-guard";

// The unit environment has no DOM: a minimal window is enough for the registry and its listeners.
const listeners = new Map<string, Set<unknown>>();
const confirm = vi.fn();
vi.stubGlobal("window", {
  addEventListener: (type: string, fn: unknown) => { (listeners.get(type) ?? listeners.set(type, new Set()).get(type)!).add(fn); },
  removeEventListener: (type: string, fn: unknown) => { listeners.get(type)?.delete(fn); },
  confirm,
});
const beforeUnloadCount = () => listeners.get("beforeunload")?.size ?? 0;
afterEach(() => confirm.mockReset());

describe("navigation guard (UX-003)", () => {
  it("registers beforeunload only while a guard is registered, once", () => {
    expect(beforeUnloadCount()).toBe(0);
    const a = registerGuard(() => true);
    const b = registerGuard(() => false);
    expect(beforeUnloadCount()).toBe(1);
    a();
    expect(beforeUnloadCount()).toBe(1);
    b();
    expect(beforeUnloadCount()).toBe(0);
  });
  it("lets navigation through without asking when nothing is dirty", () => {
    const off = registerGuard(() => false);
    expect(isDirty()).toBe(false);
    expect(confirmLeave()).toBe(true);
    expect(confirm).not.toHaveBeenCalled();
    off();
  });
  it("asks when dirty and obeys the answer", () => {
    const off = registerGuard(() => true);
    confirm.mockReturnValueOnce(false);
    expect(confirmLeave()).toBe(false);
    confirm.mockReturnValueOnce(true);
    expect(confirmLeave()).toBe(true);
    expect(confirm).toHaveBeenCalledTimes(2);
    off();
    expect(confirmLeave()).toBe(true);
  });
});
