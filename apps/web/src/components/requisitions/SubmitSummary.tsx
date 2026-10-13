import { WarningCircle } from "@phosphor-icons/react";
import { useEffect, useRef } from "react";
import type { SummaryProblem } from "./model";

/** The focused error summary (requisition-create.md section 6): `Category: link`, one item per problem. */
export default function SubmitSummary({ title, lead, problems, detail }: { title: string; lead: string; problems: readonly SummaryProblem[]; detail?: string }) {
  const ref = useRef<HTMLElement>(null);
  useEffect(() => { ref.current?.focus(); }, [problems, detail, title]);
  const go = (target: string) => {
    const element = document.getElementById(target);
    if (!element) return;
    element.scrollIntoView({ block: "center" });
    element.focus();
  };
  return <section ref={ref} className="error-summary req-summary" role="alert" tabIndex={-1} aria-labelledby="req-summary-title">
    <h2 id="req-summary-title"><WarningCircle aria-hidden="true" weight="regular" />{title}</h2>
    <p>{lead}</p>
    {detail && <p>{detail}</p>}
    {problems.length > 0 && <ul>{problems.map((p, i) => <li key={`${p.target}-${i}`}>
      <a href={`#${p.target}`} onClick={e => { e.preventDefault(); go(p.target); }}>{p.category}: {p.message}</a>
    </li>)}</ul>}
  </section>;
}
