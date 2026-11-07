import type { ReactNode } from "react";
import type { Loaded } from "../requisitions/useLoad";

/** One card with its own loading and error state, so a failing card never blanks the rest (UX-004). */
export default function Card({ title, load, children, wide }: { title: string; load: Pick<Loaded<unknown>, "error" | "loading" | "data" | "reload">; children: ReactNode; wide?: boolean }) {
  const id = `rfq-card-${title.replaceAll(" ", "-").toLowerCase()}`;
  return <section className={`req-card${wide ? " req-wide" : ""}`} aria-labelledby={id} aria-busy={load.loading || undefined}>
    <header className="req-card-head"><h2 id={id}>{title}</h2></header>
    {load.error ? <div role="alert"><p>{load.error}</p><button type="button" onClick={load.reload}>Try again</button></div>
      : load.loading && load.data === null ? <p className="req-skeleton req-skeleton-short" role="status" aria-label={`Loading ${title}`} /> : children}
  </section>;
}
