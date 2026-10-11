import { useCallback, useEffect, useState } from "react";
import { apiClient } from "../../api/client";
import type { components } from "../../api/generated/schema";
import { writeHeaders } from "../../features/auth/api";
import RecordForm, { type RecordField } from "./RecordForm";
import { failure, label, WriteFailure, type Phase, type Milestone } from "./api";

const phaseFields: RecordField[] = [
  { key: "name", label: "Phase name", help: "Name this phase.", required: true },
  { key: "sequence", label: "Sequence", help: "Optional display order.", type: "number", min: -32768, max: 32767 },
  { key: "sub_project_id", label: "Sub-project id", help: "Optional descendant sub-project identifier." },
  { key: "planned_start", label: "Planned start", help: "Optional planned date.", type: "date" },
  { key: "planned_end", label: "Planned end", help: "Optional planned date.", type: "date" },
  { key: "actual_start", label: "Actual start", help: "Optional actual date.", type: "date" },
  { key: "actual_end", label: "Actual end", help: "Optional actual date.", type: "date" },
  { key: "percent_complete", label: "Percent complete", help: "Enter a whole number from 0 to 100.", type: "number", min: 0, max: 100 },
  { key: "status", label: "Phase status", help: "Select the current phase status.", options: ["planned", "in_progress", "done", "skipped"] },
];
const milestoneFields: RecordField[] = [
  { key: "name", label: "Milestone name", help: "Name this milestone.", required: true },
  { key: "due_date", label: "Due date", help: "Date the milestone is due.", type: "date", required: true },
  { key: "phase_id", label: "Phase id", help: "Optional phase identifier from the list above." },
  { key: "completed_on", label: "Completed on", help: "Optional completion date.", type: "date" },
];
function values(row: Phase | Milestone): Record<string, string> {
  return Object.fromEntries(Object.entries(row).map(([key, value]) => [key, value === null ? "" : String(value)]));
}
export default function Schedule({ id }: { id: string }) {
  const [phases, setPhases] = useState<Phase[] | null>(null);
  const [milestones, setMilestones] = useState<Milestone[] | null>(null);
  const [errors, setErrors] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [editor, setEditor] = useState<{ kind: "phase" | "milestone"; row?: Phase | Milestone } | null>(null);
  const load = useCallback(async () => {
    const results = await Promise.allSettled([
      apiClient.GET("/api/v1/projects/{id}/phases", { params: { path: { id } } }),
      apiClient.GET("/api/v1/projects/{id}/milestones", { params: { path: { id } } }),
    ]);
    const problems: string[] = [];
    const phase = results[0]; const milestone = results[1];
    if (phase.status === "fulfilled" && phase.value.data) setPhases(phase.value.data);
    else problems.push(failure(phase.status === "fulfilled" ? phase.value.error : undefined, "Loading phases"));
    if (milestone.status === "fulfilled" && milestone.value.data) setMilestones(milestone.value.data);
    else problems.push(failure(milestone.status === "fulfilled" ? milestone.value.error : undefined, "Loading milestones"));
    setErrors(problems); setLoading(false);
  }, [id]);
  // load updates state only after network responses; this effect starts the request.
  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => { void load(); }, [load]);
  const save = async (v: Record<string, string>) => {
    if (!editor) return;
    if (editor.kind === "phase") {
      const body: components["schemas"]["PhaseCreate"] = {
        name: v.name!, sub_project_id: v.sub_project_id || null, sequence: v.sequence ? Number(v.sequence) : undefined,
        planned_start: v.planned_start || null, planned_end: v.planned_end || null,
        actual_start: v.actual_start || null, actual_end: v.actual_end || null,
        percent_complete: Number(v.percent_complete || "0"), status: (v.status || "planned") as Phase["status"],
      };
      const result = editor.row ? await apiClient.PATCH("/api/v1/projects/{id}/phases/{phase_id}", { params: { path: { id, phase_id: editor.row.id } }, headers: writeHeaders(), body })
        : await apiClient.POST("/api/v1/projects/{id}/phases", { params: { path: { id } }, headers: writeHeaders(), body });
      if (!result.data) throw new WriteFailure(result.error, "Saving phase");
    } else {
      const body: components["schemas"]["MilestoneCreate"] = { name: v.name!, due_date: v.due_date!, phase_id: v.phase_id || null, completed_on: v.completed_on || null };
      const result = editor.row ? await apiClient.PATCH("/api/v1/projects/{id}/milestones/{mid}", { params: { path: { id, mid: editor.row.id } }, headers: writeHeaders(), body })
        : await apiClient.POST("/api/v1/projects/{id}/milestones", { params: { path: { id } }, headers: writeHeaders(), body });
      if (!result.data) throw new WriteFailure(result.error, "Saving milestone");
    }
    setEditor(null); setLoading(true); await load();
  };
  return <section aria-label="Project schedule">
    {loading && <p role="status">Loading schedule… Previously loaded rows are preserved.</p>}
    {errors.length > 0 && <div role="alert">{errors.map(e => <p key={e}>{e}</p>)}<button onClick={() => { setLoading(true); void load(); }}>Retry schedule</button></div>}
    {editor && <RecordForm key={`${editor.kind}-${editor.row?.id ?? "new"}`} title={`${editor.row ? "Edit" : "Create"} ${editor.kind}`} fields={editor.kind === "phase" ? phaseFields : milestoneFields} initial={editor.row ? values(editor.row) : {}} save={save} cancel={() => setEditor(null)} />}
    <h3>Phases</h3><button onClick={() => setEditor({ kind: "phase" })}>Create phase</button>
    {phases?.length === 0 && <p>No phases yet. Create a phase to plan the schedule.</p>}
    {phases && phases.length > 0 && <ul className="project-records">{phases.map(row => <li key={row.id}><strong>{row.name}</strong><p>{row.id} · {label(row.status ?? "unknown")} · {row.percent_complete}% complete</p><p>{row.planned_start ?? "Unscheduled"} – {row.planned_end ?? "Unscheduled"} · Variance: {row.schedule_variance_days ?? "—"} days</p><button onClick={() => setEditor({ kind: "phase", row })}>Edit {row.name}</button></li>)}</ul>}
    <h3>Milestones</h3><button onClick={() => setEditor({ kind: "milestone" })}>Create milestone</button>
    {milestones?.length === 0 && <p>No milestones yet. Create a milestone to track delivery.</p>}
    {milestones && milestones.length > 0 && <ul className="project-records">{milestones.map(row => <li key={row.id}><strong>{row.name}</strong><p>Due {row.due_date} · Completed {row.completed_on ?? "—"}</p><button onClick={() => setEditor({ kind: "milestone", row })}>Edit {row.name}</button></li>)}</ul>}
  </section>;
}
