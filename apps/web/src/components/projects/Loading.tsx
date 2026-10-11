export default function ProjectLoading() {
  return <div role="status" aria-label="Loading project" className="project-loading">
    <span className="visually-hidden">Loading project details and available actions</span>
    <div aria-hidden="true">
      <div className="project-record-heading"><span className="project-skeleton" /></div>
      <div className="project-tabs"><span className="project-skeleton" /></div>
      <h2>Overview</h2>
      <dl className="project-facts">{Array.from({ length: 14 }, (_, index) => <div key={index}><dt><span className="project-skeleton" /></dt><dd><span className="project-skeleton" /></dd></div>)}</dl>
    </div>
  </div>;
}
