import { useCallback, useEffect, useRef, useState } from "react";

export type Loaded<T> = { data: T | null; error: string | null; loading: boolean; reload: () => void; set: (value: T) => void };

/** One section's request: its own loading and error state, so a failing card never blanks the others (UX-004). */
export function useLoad<T>(fetcher: () => Promise<T>, deps: readonly unknown[]): Loaded<T> {
  const [state, setState] = useState<{ data: T | null; error: string | null; loading: boolean }>({ data: null, error: null, loading: true });
  const [tick, setTick] = useState(0);
  const latest = useRef(fetcher);
  useEffect(() => { latest.current = fetcher; });
  const key = JSON.stringify(deps);
  useEffect(() => {
    let active = true;
    // The effect starts the request; state is set only from its response.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setState(s => ({ ...s, loading: true }));
    latest.current().then(
      data => { if (active) setState({ data, error: null, loading: false }); },
      (e: unknown) => { if (active) setState(s => ({ data: s.data, error: e instanceof Error ? e.message : "Loading failed.", loading: false })); },
    );
    return () => { active = false; };
  }, [key, tick]);
  const reload = useCallback(() => setTick(n => n + 1), []);
  const set = useCallback((data: T) => setState({ data, error: null, loading: false }), []);
  return { ...state, reload, set };
}
