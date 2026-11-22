import { useEffect, useRef, useState, type RefObject } from "react";

export function isEditableTarget(target: EventTarget | null): boolean {
  return (
    target instanceof HTMLElement &&
    (target.isContentEditable || /^(INPUT|SELECT|TEXTAREA)$/.test(target.tagName))
  );
}

export function restoreFocus(target: HTMLElement | null): void {
  if (target?.isConnected) target.focus();
}

/** Focus a target once the DOM has it (E13-S33): the control that held focus is gone, so never leave it on the body. */
export function focusSoon(target: () => HTMLElement | null | undefined): void {
  requestAnimationFrame(() => target()?.focus());
}

export function useFocusOnRouteChange(
  routeKey: string,
  title: string,
  mainRef: RefObject<HTMLElement | null>,
): string {
  const previousRoute = useRef<string | null>(null);
  const [announcement, setAnnouncement] = useState("");

  useEffect(() => {
    if (previousRoute.current === null) {
      previousRoute.current = routeKey;
      // First route after sign-in: focus main only if nothing else (a deep link's control) already has focus.
      const active = document.activeElement;
      if (active && active !== document.body) return;
      // Not cancelled on cleanup: StrictMode runs this effect twice and the second run takes the equal-route early return.
      // Re-check in the frame: a control focused since this effect ran keeps focus.
      requestAnimationFrame(() => { const now = document.activeElement; if (!now || now === document.body) mainRef.current?.focus(); });
      return;
    }
    if (previousRoute.current === routeKey) return;
    previousRoute.current = routeKey;

    const frame = requestAnimationFrame(() => {
      mainRef.current?.focus();
      setAnnouncement(title);
    });
    return () => cancelAnimationFrame(frame);
  }, [mainRef, routeKey, title]);

  return announcement;
}
