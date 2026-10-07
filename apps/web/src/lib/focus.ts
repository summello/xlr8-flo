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
