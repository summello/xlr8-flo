import type { Performance } from "./api";

type Props = { performance: Performance; onOpen: (opener: HTMLElement) => void };

/** Card body for /vendors/:id. Every figure is the server's string; nothing is averaged here (VEN-007). */
export default function PerformanceCard({ performance, onOpen }: Props) {
  return <>
    {performance.overall === null
      ? <p>No scores yet. Scores come from completed orders.</p>
      : <>
        <p className="perf-overall"><strong>{performance.overall}</strong> <span className="req-sub">out of 5 overall · {performance.count} {performance.count === 1 ? "score" : "scores"} · All periods</span></p>
        <table className="vendor-table perf-table">
          <caption className="visually-hidden">Average score by category</caption>
          <thead><tr><th scope="col">Category</th><th scope="col" className="perf-num">Average</th><th scope="col" className="perf-num">Scores</th><th scope="col">Period</th></tr></thead>
          <tbody>{performance.categories.map(c => <tr key={c.category_code}>
            <th scope="row">{c.category_code}</th><td className="perf-num">{c.average}</td><td className="perf-num">{c.count}</td><td>All periods</td>
          </tr>)}</tbody>
        </table>
      </>}
    <button type="button" onClick={e => onOpen(e.currentTarget)}>Open Scorecard</button>
  </>;
}
